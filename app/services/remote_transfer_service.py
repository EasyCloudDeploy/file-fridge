import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import httpx
import zstandard as zstd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    FileInventory,
    FileStatus,
    FileTransferStrategy,
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    TransferDirection,
    TransferStatus,
)
from app.services.file_metadata import file_metadata_extractor
from app.utils.remote_signature import get_signed_headers
from app.utils.retry_strategy import retry_strategy

logger = logging.getLogger(__name__)

CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
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
        direction: TransferDirection = TransferDirection.PUSH,
        strategy: FileTransferStrategy = FileTransferStrategy.COPY,
        conflict_resolution: ConflictResolution = ConflictResolution.OVERWRITE,
    ) -> RemoteTransferJob:
        """Create a new remote transfer job."""
        logger.info(
            f"Creating transfer job: file_id={file_id}, "
            f"remote_connection_id={remote_connection_id}, "
            f"remote_monitored_path_id={remote_monitored_path_id}"
        )

        # Get the file from inventory
        file_obj = db.query(FileInventory).filter(FileInventory.id == file_id).first()
        if not file_obj:
            msg = f"File with ID {file_id} not found in inventory"
            logger.error(msg)
            raise ValueError(msg)

        # Validate remote connection exists
        conn = (
            db.query(RemoteConnection).filter(RemoteConnection.id == remote_connection_id).first()
        )
        if not conn:
            msg = f"Remote connection with ID {remote_connection_id} not found"
            logger.error(msg)
            raise ValueError(msg)

        logger.debug(f"Using remote connection: {conn.name} ({conn.url})")

        # Get the monitored path to determine relative path
        monitored_path = (
            db.query(MonitoredPath).filter(MonitoredPath.id == file_obj.path_id).first()
        )
        if not monitored_path:
            msg = f"Monitored path with ID {file_obj.path_id} not found"
            logger.error(msg)
            raise ValueError(msg)

        logger.debug(f"Source monitored path: {monitored_path.name} ({monitored_path.source_path})")

        # Calculate relative path
        source_path = Path(file_obj.file_path)
        monitored_source = Path(monitored_path.source_path)
        try:
            relative_path = str(source_path.relative_to(monitored_source))
            logger.debug(f"Relative path: {relative_path}")
        except ValueError as err:
            msg = f"File path {source_path} is not relative to monitored path {monitored_source}"
            logger.error(msg)
            raise ValueError(msg) from err

        # Get file size
        if not source_path.exists():
            msg = f"Source file does not exist: {source_path}"
            logger.error(msg)
            raise ValueError(msg)

        file_size = source_path.stat().st_size
        logger.debug(f"File size: {file_size} bytes")

        # Compute checksum if not already present
        checksum = file_obj.checksum
        if not checksum:
            logger.info(f"Computing checksum for {source_path}")
            checksum = file_metadata_extractor.compute_sha256(source_path)
        else:
            logger.debug(f"Using existing checksum: {checksum}")

        # Create the transfer job
        job = RemoteTransferJob(
            file_inventory_id=file_id,
            remote_connection_id=remote_connection_id,
            remote_monitored_path_id=remote_monitored_path_id,
            status=TransferStatus.PENDING,
            progress=0,
            current_size=0,
            total_size=file_size,
            source_path=str(source_path),
            relative_path=relative_path,
            storage_type=file_obj.storage_type,
            checksum=checksum,
            direction=direction,
            strategy=strategy,
            conflict_resolution=conflict_resolution,
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        logger.info(
            f"Created transfer job {job.id} ({direction.value}) for file "
            f"{file_obj.file_path} to remote connection {conn.name}"
        )
        return job

    async def process_pending_transfers(self):
        """Process pending transfer jobs."""
        db = SessionLocal()
        try:
            # Get all pending jobs
            pending_jobs = (
                db.query(RemoteTransferJob)
                .filter(RemoteTransferJob.status == TransferStatus.PENDING)
                .all()
            )

            if not pending_jobs:
                logger.debug("No pending transfer jobs found")
                return

            logger.info(f"Found {len(pending_jobs)} pending transfer job(s)")

            # Process each job in parallel (up to a reasonable limit)
            tasks = []
            for job in pending_jobs[:10]:  # Process max 10 jobs concurrently
                logger.info(f"Starting transfer job {job.id}")
                # Update status to in_progress
                job.status = TransferStatus.IN_PROGRESS
                job.start_time = datetime.now(timezone.utc)
                db.commit()

                # Launch the transfer as an async task
                tasks.append(self.run_transfer(job.id))

            # Wait for all tasks to complete
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception:
            logger.exception("Error processing pending transfers")
        finally:
            db.close()

    async def _is_final_chunk(self, file_handle) -> bool:
        """Check if we've reached the end of the file."""
        current_pos = await file_handle.tell()
        await file_handle.seek(0, 2)  # Seek to end
        end_pos = await file_handle.tell()
        await file_handle.seek(current_pos)  # Seek back to original position
        return current_pos >= end_pos

    def _update_job_progress(
        self, job: RemoteTransferJob, bytes_transferred: int, start_time: float, db: Session
    ):
        """Update job progress, speed, and ETA."""
        job.current_size += bytes_transferred
        job.progress = int((job.current_size / job.total_size) * 100) if job.total_size > 0 else 0

        # Calculate transfer speed (bytes per second)
        elapsed_time = time.time() - start_time
        if elapsed_time > 0:
            job.current_speed = int(job.current_size / elapsed_time)

            # Calculate ETA (seconds remaining)
            if job.current_speed > 0:
                bytes_remaining = job.total_size - job.current_size
                job.eta = int(bytes_remaining / job.current_speed)
            else:
                job.eta = None
        else:
            job.current_speed = 0
            job.eta = None

        db.commit()

    def _perform_ecdh_key_exchange(self, conn: RemoteConnection):
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

    def _compress_and_encrypt_chunk(self, chunk, use_encryption, aesgcm_key):
        """Compress and optionally encrypt a chunk using the derived session key."""
        cctx = zstd.ZstdCompressor(level=3)
        final_chunk = cctx.compress(chunk)
        nonce = b""
        if use_encryption:
            nonce = os.urandom(12)
            aesgcm = AESGCM(aesgcm_key)
            final_chunk = aesgcm.encrypt(nonce, final_chunk, None)
        return final_chunk, nonce

    async def _get_remote_status(
        self, db: Session, client: httpx.AsyncClient, conn: RemoteConnection, job: RemoteTransferJob
    ):
        """Check remote status for resumability."""
        url = f"{conn.url.rstrip('/')}/api/v1/remote/transfer-status"
        params = {
            "relative_path": job.relative_path,
            "remote_path_id": str(job.remote_monitored_path_id),
            "storage_type": job.storage_type.value,
        }
        # Build a request to sign it
        req = httpx.Request("GET", url, params=params)
        signed_headers = await get_signed_headers(db, req.method, str(req.url), req.content)

        try:
            response = await client.get(
                url, params=params, headers=signed_headers, timeout=get_transfer_timeouts()
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug(f"Failed to get remote status for job {job.id}, starting fresh")
            return {"size": 0, "status": "not_found"}

    async def _send_chunks(
        self, job: RemoteTransferJob, conn: RemoteConnection, db: Session, client: httpx.AsyncClient
    ):
        """Internal helper to send file chunks."""
        use_encryption = not conn.url.startswith("https://")
        ephemeral_pub_key_b64, session_key = None, None
        if use_encryption:
            ephemeral_pub_key_b64, session_key = self._perform_ecdh_key_exchange(conn)

        start_time_ts = time.time()
        source_path = Path(job.source_path)

        remote_status = await self._get_remote_status(db, client, conn, job)
        remote_size = remote_status.get("size", 0)

        # Initialize chunk index
        chunk_idx = 0

        async with aiofiles.open(source_path, "rb") as f:
            # Resume from where we left off if remote has partial data
            if remote_size > 0:
                logger.info(f"Resuming transfer from byte {remote_size}")
                await f.seek(remote_size)
                job.current_size = remote_size
                chunk_idx = remote_size // CHUNK_SIZE
                db.commit()

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
                    chunk, use_encryption, session_key
                )

                # --- Signing and Header construction ---
                url = f"{conn.url.rstrip('/')}/api/v1/remote/receive"
                signed_headers = await get_signed_headers(db, "POST", url, final_chunk)

                headers = {
                    "X-Job-ID": str(job.id),
                    "X-Chunk-Index": str(chunk_idx),
                    "X-Is-Final": "true" if is_final else "false",
                    "X-Relative-Path": job.relative_path,
                    "X-Remote-Path-ID": str(job.remote_monitored_path_id),
                    "X-Storage-Type": job.storage_type.value,
                    "X-Encryption-Nonce": nonce.hex() if use_encryption else "",
                    "X-File-Size": str(job.total_size),
                    **signed_headers,
                }
                if use_encryption:
                    headers["X-Ephemeral-Public-Key"] = ephemeral_pub_key_b64
                # --- End Header Construction ---

                logger.debug(
                    f"Sending chunk {chunk_idx} for job {job.id} to {url} "
                    f"(size: {len(final_chunk)} bytes, is_final: {is_final})"
                )
                logger.debug(
                    f"Request headers for chunk {chunk_idx}: "
                    f"X-Remote-Path-ID={headers.get('X-Remote-Path-ID')}, "
                    f"X-Storage-Type={headers.get('X-Storage-Type')}, "
                    f"X-Relative-Path={headers.get('X-Relative-Path')}, "
                    f"X-Job-ID={headers.get('X-Job-ID')}"
                )

                if chunk_idx == 0:
                    logger.debug(f"Sending first chunk with headers: {headers}")

                response = await client.post(
                    url,
                    headers=headers,
                    content=final_chunk,
                    timeout=get_transfer_timeouts(),
                )

                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    # Log the full error response for debugging
                    error_detail = response.text if response.text else "No error details provided"
                    logger.error(
                        f"Chunk {chunk_idx} upload failed for job {job.id}: "
                        f"Status {response.status_code}, Response: {error_detail}"
                    )
                    raise

                chunk_idx += 1
                self._update_job_progress(job, len(chunk), start_time_ts, db)

                # Log progress at reasonable intervals (approx every 10%)
                if job.total_size > 0:
                    chunks_per_10_percent = max(1, (job.total_size // CHUNK_SIZE) // 10)
                    if chunk_idx % chunks_per_10_percent == 0 or is_final:
                        eta_str = f", ETA: {job.eta}s" if job.eta is not None else ""
                        speed_mb = job.current_speed / (1024 * 1024) if job.current_speed else 0
                        logger.info(
                            f"Transfer Job {job.id} progress: {job.progress}% "
                            f"({job.current_size}/{job.total_size} bytes, "
                            f"Speed: {speed_mb:.2f} MB/s{eta_str})"
                        )

    async def run_transfer(self, job_id: int):
        db = SessionLocal()
        try:
            # Get the job
            job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
            if not job:
                logger.error(f"Transfer job {job_id} not found")
                return

            logger.info(
                f"Starting transfer job {job_id} ({job.direction.value}): "
                f"file={job.source_path}, "
                f"remote_path_id={job.remote_monitored_path_id}, "
                f"storage_type={job.storage_type.value}, "
                f"size={job.total_size} bytes"
            )

            # Get the remote connection
            conn = (
                db.query(RemoteConnection)
                .filter(RemoteConnection.id == job.remote_connection_id)
                .first()
            )
            if not conn:
                logger.error(
                    f"Remote connection {job.remote_connection_id} not found for job {job_id}"
                )
                job.status = TransferStatus.FAILED
                job.error_message = f"Remote connection {job.remote_connection_id} not found"
                job.end_time = datetime.now(timezone.utc)
                db.commit()
                return

            logger.debug(f"Transfer job {job_id} using connection: {conn.name} ({conn.url})")

            # Ensure source file still exists
            source_path = Path(job.source_path)
            if not source_path.exists():
                logger.error(f"Source file {source_path} not found for job {job_id}")
                job.status = TransferStatus.FAILED
                job.error_message = f"Source file not found: {source_path}"
                job.end_time = datetime.now(timezone.utc)
                db.commit()
                return

            # Retry loop with exponential backoff
            for attempt in range(MAX_RETRIES):
                try:
                    async with httpx.AsyncClient(timeout=get_transfer_timeouts()) as client:
                        await self._send_chunks(job, conn, db, client)

                        # Finalize
                        url = f"{conn.url.rstrip('/')}/api/v1/remote/verify-transfer"
                        json_payload = {
                            "job_id": job.id,
                            "checksum": job.checksum,
                            "relative_path": job.relative_path,
                            "remote_path_id": job.remote_monitored_path_id,
                        }
                        body_bytes = json.dumps(json_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        signed_headers = await get_signed_headers(
                            db, "POST", url, body_bytes
                        )

                        verify_response = await client.post(
                            url,
                            headers=signed_headers,
                            content=body_bytes,
                        )
                        verify_response.raise_for_status()

                        # Transfer successful!
                        logger.info(f"Transfer job {job.id} completed successfully")
                        job.status = TransferStatus.COMPLETED
                        job.progress = 100
                        job.end_time = datetime.now(timezone.utc)
                        job.error_message = None
                        db.commit()

                        # Optional cleanup
                        await self._cleanup_after_transfer(db, job, conn)
                        return

                except Exception as e:
                    logger.warning(
                        f"Transfer job {job.id} attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                    )
                    job.retry_count = attempt + 1
                    db.commit()

                    # Determine if we should retry
                    should_retry, delay, reason = retry_strategy.should_retry(attempt + 1, e)

                    if not should_retry:
                        logger.exception(f"Transfer job {job.id} failed permanently: {reason}")
                        job.status = TransferStatus.FAILED
                        job.error_message = f"{reason}: {e!s}"
                        job.end_time = datetime.now(timezone.utc)
                        db.commit()
                        return

                    # Wait before retrying
                    logger.info(f"Transfer job {job.id}: {reason}")
                    await asyncio.sleep(delay)

            # If we get here, all retries failed
            logger.error(f"Transfer job {job.id} failed after {MAX_RETRIES} attempts")
            job.status = TransferStatus.FAILED
            job.error_message = f"Transfer failed after {MAX_RETRIES} retry attempts"
            job.end_time = datetime.now(timezone.utc)
            db.commit()

        except Exception:
            logger.exception(f"Unexpected error in transfer job {job_id}")
            try:
                job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
                if job:
                    job.status = TransferStatus.FAILED
                    job.error_message = "Unexpected error during transfer"
                    job.end_time = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                logger.exception("Failed to update job status after error")
        finally:
            db.close()

    def cancel_transfer(self, db: Session, job_id: int) -> bool:
        """
        Cancel a transfer job.

        Args:
            db: Database session
            job_id: ID of the transfer job to cancel

        Returns:
            True if the job was cancelled, False otherwise
        """
        job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
        if not job:
            logger.warning(f"Transfer job {job_id} not found for cancellation")
            return False

        # Can only cancel pending or in-progress jobs
        if job.status not in (TransferStatus.PENDING, TransferStatus.IN_PROGRESS):
            logger.warning(
                f"Transfer job {job_id} cannot be cancelled (status: {job.status.value})"
            )
            return False

        logger.info(f"Cancelling transfer job {job_id}")
        job.status = TransferStatus.CANCELLED
        job.end_time = datetime.now(timezone.utc)
        job.error_message = "Transfer cancelled by user"
        db.commit()

        return True

    async def _cleanup_after_transfer(
        self, db: Session, job: RemoteTransferJob, _conn: RemoteConnection
    ) -> None:
        """
        Cleanup after successful transfer.
        If the strategy is MOVE, delete the source file safely.
        """
        if job.strategy == FileTransferStrategy.MOVE:
            source_path = Path(job.source_path)
            if source_path.exists():
                logger.info(
                    f"Transfer job {job.id} used MOVE strategy. Removing source file {source_path}"
                )
                try:
                    # Remove file asynchronously
                    await aiofiles.os.remove(str(source_path))

                    # Update file inventory status
                    file_obj = (
                        db.query(FileInventory)
                        .filter(FileInventory.id == job.file_inventory_id)
                        .first()
                    )
                    if file_obj:
                        file_obj.status = FileStatus.MOVED
                        db.commit()
                        logger.info(f"Marked file {job.file_inventory_id} as MOVED in inventory")
                except Exception as e:
                    logger.error(f"Failed to remove source file {source_path} after MOVE: {e}")
            else:
                logger.warning(
                    f"Transfer job {job.id} MOVE task: source file {source_path} already missing"
                )

        logger.debug(f"Cleanup completed for transfer job {job.id}")


remote_transfer_service = RemoteTransferService()
