import asyncio
import base64
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import anyio
import httpx
import zstandard as zstd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.hashes import SHA256
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    FileInventory,
    FileStatus,
    RemoteConnection,
    RemoteTransferJob,
    TransferStatus,
)
from app.services.audit_trail_service import audit_trail_service
from app.services.identity_service import identity_service
from app.utils.circuit_breaker import get_circuit_breaker
from app.utils.remote_signature import get_signed_headers
from app.utils.retry_strategy import retry_strategy

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1MB chunks
MAX_RETRIES = retry_strategy.max_retries


def get_transfer_timeouts() -> httpx.Timeout:
    """Return a standard httpx.Timeout object for remote transfers."""
    return httpx.Timeout(
        connect=settings.remote_transfer_connect_timeout,
        read=settings.remote_transfer_read_timeout,
        write=settings.remote_transfer_write_timeout,
        pool=settings.remote_transfer_pool_timeout,
    )


class RemoteTransferService:
    """Service for sending files to remote File Fridge instances."""

    def create_transfer_job(
        self,
        db: Session,
        file_id: int,
        remote_connection_id: int,
        remote_monitored_path_id: int,
    ) -> RemoteTransferJob:
        """Create a new remote transfer job."""
        # ... (implementation remains the same)
        pass

    async def process_pending_transfers(self):
        """Process pending transfer jobs."""
        # ... (implementation remains the same)
        pass

    def _perform_ecdh_key_exchange(self, db: Session, conn: RemoteConnection):
        """
        Performs an ECDH key exchange to derive a symmetric key for this transfer.
        Returns the ephemeral public key to be sent and the derived symmetric key.
        """
        # 1. Generate an ephemeral key pair for this session
        ephemeral_private_key = x25519.X25519PrivateKey.generate()
        ephemeral_public_key = ephemeral_private_key.public_key()

        # 2. Load the remote's main public key
        remote_public_key_bytes = base64.b64decode(conn.remote_x25519_public_key)
        remote_public_key = x25519.X25519PublicKey.from_public_bytes(remote_public_key_bytes)

        # 3. Perform ECDH to get the shared secret
        shared_secret = ephemeral_private_key.exchange(remote_public_key)

        # 4. Use HKDF to derive a 32-byte key for AES-GCM
        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=None,
            info=b"file-fridge-transfer-key",
        )
        symmetric_key = hkdf.derive(shared_secret)

        # 5. Serialize the ephemeral public key to send to the remote
        ephemeral_public_key_b64 = base64.b64encode(
            ephemeral_public_key.public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
            )
        ).decode("ascii")

        return ephemeral_public_key_b64, symmetric_key

    def _compress_and_encrypt_chunk(self, chunk, cctx, use_encryption, aesgcm_key):
        """Compress and optionally encrypt a chunk using the derived session key."""
        final_chunk = cctx.compress(chunk)
        nonce = b""
        if use_encryption:
            nonce = os.urandom(12)
            aesgcm = AESGCM(aesgcm_key)
            final_chunk = aesgcm.encrypt(nonce, final_chunk, None)
        return final_chunk, nonce

    async def _get_remote_status(self, db: Session, client: httpx.AsyncClient, conn: RemoteConnection, job: RemoteTransferJob):
        """Check remote status for resumability."""
        url = f"{conn.url.rstrip('/')}/api/remote/transfer-status"
        params = {
            "relative_path": job.relative_path,
            "remote_path_id": str(job.remote_monitored_path_id),
            "storage_type": job.storage_type.value,
        }
        # Build a request to sign it
        req = httpx.Request("GET", url, params=params)
        signed_headers = await get_signed_headers(db, req.method, str(req.url), req.content)

        try:
            response = await client.get(url, params=params, headers=signed_headers, timeout=get_transfer_timeouts())
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug(f"Failed to get remote status for job {job.id}, starting fresh")
            return {"size": 0, "status": "not_found"}

    async def _send_chunks(self, job, conn, db, client):
        """Internal helper to send file chunks."""
        use_encryption = not conn.url.startswith("https://")
        ephemeral_pub_key_b64, session_key = None, None
        if use_encryption:
            ephemeral_pub_key_b64, session_key = self._perform_ecdh_key_exchange(db, conn)

        cctx = zstd.ZstdCompressor()
        start_time_ts = time.time()
        source_path = Path(job.source_path)

        remote_status = await self._get_remote_status(db, client, conn, job)
        remote_size = remote_status.get("size", 0)

        async with aiofiles.open(source_path, "rb") as f:
            # ... (resume logic is the same)

            while True:
                chunk = await f.read(CHUNK_SIZE)
                if not chunk:
                    break
                
                db.refresh(job)
                if job.status == TransferStatus.CANCELLED:
                    logger.info(f"Transfer {job.id} was cancelled, stopping")
                    return

                is_final = await self._is_final_chunk(f)
                final_chunk, nonce = self._compress_and_encrypt_chunk(
                    chunk, cctx, use_encryption, session_key
                )
                
                # --- Signing and Header construction ---
                url = f"{conn.url.rstrip('/')}/api/remote/receive"
                signed_headers = await get_signed_headers(db, "POST", url, final_chunk)

                headers = {
                    "X-Job-ID": str(job.id),
                    "X-Chunk-Index": str(chunk_idx),
                    "X-Is-Final": "true" if is_final else "false",
                    "X-Relative-Path": job.relative_path,
                    "X-Remote-Path-ID": str(job.remote_monitored_path_id),
                    "X-Storage-Type": job.storage_type.value,
                    "X-Nonce": nonce.hex() if use_encryption else "",
                    "X-File-Size": str(job.total_size),
                    **signed_headers,
                }
                if use_encryption and chunk_idx == 0:
                    headers["X-Ephemeral-Public-Key"] = ephemeral_pub_key_b64
                # --- End Header Construction ---

                async def chunk_generator():
                    yield final_chunk

                response = await client.post(
                    url,
                    headers=headers,
                    content=chunk_generator(),
                    timeout=get_transfer_timeouts(),
                )
                response.raise_for_status()

                chunk_idx += 1
                self._update_job_progress(job, len(chunk), start_time_ts, db)

    async def run_transfer(self, job_id: int):
        db = SessionLocal()
        try:
            # ... (setup logic is the same, but remove my_uuid)
            
            # Retry loop with exponential backoff
            for attempt in range(MAX_RETRIES):
                try:
                    async with httpx.AsyncClient() as client:
                        await self._send_chunks(job, conn, db, client)

                        # Finalize
                        url = f"{conn.url.rstrip('/')}/api/remote/verify-transfer"
                        json_payload = {
                            "job_id": job.id,
                            "checksum": job.checksum,
                            "relative_path": job.relative_path,
                            "remote_path_id": job.remote_monitored_path_id,
                        }
                        signed_headers = await get_signed_headers(db, "POST", url, str(json_payload).encode('utf-8'))
                        
                        verify_response = await client.post(
                            url,
                            headers=signed_headers,
                            json=json_payload,
                            timeout=get_transfer_timeouts(),
                        )
                        verify_response.raise_for_status()

                        # ... (rest of the success logic is the same)
                        return

                except Exception as e:
                    # ... (error handling is the same)
                    pass
        finally:
            db.close()
            
    # ... (other methods are mostly the same)
    def cancel_transfer(self, db: Session, job_id: int):
        pass
    async def _cleanup_after_transfer(self, db, job, conn):
        pass


remote_transfer_service = RemoteTransferService()
