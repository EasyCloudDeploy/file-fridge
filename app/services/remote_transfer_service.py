import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import anyio
import httpx
import zstandard as zstd
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    FileInventory,
    FileStatus,
    RemoteTransferJob,
    TransferStatus,
)
from app.services.audit_trail_service import audit_trail_service

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1MB chunks
MAX_RETRIES = 3


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
        file_item = db.query(FileInventory).filter(FileInventory.id == file_id).first()
        if not file_item:
            msg = "File not found in inventory"
            raise ValueError(msg)

        # Get MonitoredPath to calculate relative path
        path = file_item.path
        try:
            relative_path = os.path.relpath(file_item.file_path, path.source_path)
        except ValueError:
            # Fallback if paths are on different drives on Windows, though this is for Linux
            relative_path = Path(file_item.file_path).name

        job = RemoteTransferJob(
            file_inventory_id=file_id,
            remote_connection_id=remote_connection_id,
            remote_monitored_path_id=remote_monitored_path_id,
            status=TransferStatus.PENDING,
            total_size=file_item.file_size,
            source_path=file_item.file_path,
            relative_path=relative_path,
            storage_type=file_item.storage_type,
            checksum=file_item.checksum,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    async def process_pending_transfers(self):
        """Process pending transfer jobs."""
        db = SessionLocal()
        try:
            jobs = (
                db.query(RemoteTransferJob)
                .filter(
                    RemoteTransferJob.status.in_([TransferStatus.PENDING, TransferStatus.FAILED])
                )
                .filter(RemoteTransferJob.retry_count < MAX_RETRIES)
                .all()
            )
            for job in jobs:
                try:
                    await self.run_transfer(job.id)
                except Exception:
                    logger.exception(f"Error processing transfer job {job.id}")
        finally:
            db.close()

    def _prepare_encryption(self, conn):
        """Prepare encryption components if needed."""
        use_encryption = not conn.url.startswith("https://")
        aesgcm = None
        if use_encryption:
            key = bytes.fromhex(conn.shared_secret)[:32]
            aesgcm = AESGCM(key)
        return use_encryption, aesgcm

    def _compress_and_encrypt_chunk(self, chunk, cctx, use_encryption, aesgcm):
        """Compress and optionally encrypt a chunk."""
        final_chunk = cctx.compress(chunk)
        nonce = b""
        if use_encryption:
            nonce = os.urandom(12)
            final_chunk = aesgcm.encrypt(nonce, final_chunk, None)
        return final_chunk, nonce

    def _build_chunk_headers(self, conn, job, chunk_idx, is_final, nonce):
        """Build headers for chunk transmission."""
        my_url = settings.ff_instance_url or "http://localhost:8000"
        use_encryption = bool(nonce)
        return {
            "X-Remote-ID": my_url,
            "X-Shared-Secret": conn.shared_secret,
            "X-Job-ID": str(job.id),
            "X-Chunk-Index": str(chunk_idx),
            "X-Is-Final": "true" if is_final else "false",
            "X-Relative-Path": job.relative_path,
            "X-Remote-Path-ID": str(job.remote_monitored_path_id),
            "X-Storage-Type": job.storage_type.value,
            "X-Nonce": nonce.hex() if use_encryption else "",
        }

    def _update_job_progress(self, job, chunk_size, start_time_ts, db):
        """Update job progress metrics."""
        job.current_size += chunk_size
        job.progress = int((job.current_size / job.total_size) * 100)
        self._update_speed_eta(job, start_time_ts)
        db.commit()

    async def _send_chunks(self, job, conn, db, client):
        """Internal helper to send file chunks."""
        use_encryption, aesgcm = self._prepare_encryption(conn)
        cctx = zstd.ZstdCompressor()
        start_time_ts = time.time()
        source_path = Path(job.source_path)

        async with aiofiles.open(source_path, "rb") as f:
            chunk_idx = 0
            while True:
                chunk = await f.read(CHUNK_SIZE)
                if not chunk:
                    break

                is_final = await self._is_final_chunk(f)
                final_chunk, nonce = self._compress_and_encrypt_chunk(
                    chunk, cctx, use_encryption, aesgcm
                )
                headers = self._build_chunk_headers(conn, job, chunk_idx, is_final, nonce)

                response = await client.post(
                    f"{conn.url.rstrip('/')}/api/remote/receive",
                    headers=headers,
                    content=final_chunk,
                    timeout=60.0,
                )
                response.raise_for_status()

                chunk_idx += 1
                self._update_job_progress(job, len(chunk), start_time_ts, db)

    async def _is_final_chunk(self, f):
        """Check if we are at the end of the file."""
        chunk = await f.read(1)
        if not chunk:
            return True
        await f.seek(-1, os.SEEK_CUR)
        return False

    def _update_speed_eta(self, job, start_time_ts):
        """Update job speed and ETA."""
        elapsed = time.time() - start_time_ts
        if elapsed > 0:
            speed = job.current_size / elapsed
            job.current_speed = int(speed)
            remaining_bytes = job.total_size - job.current_size
            if speed > 0:
                job.eta = int(remaining_bytes / speed)

    async def run_transfer(self, job_id: int):
        """Execute a transfer job."""
        my_url = settings.ff_instance_url or "http://localhost:8000"
        db = SessionLocal()
        try:
            job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
            if not job:
                return

            job.status = TransferStatus.IN_PROGRESS
            job.start_time = datetime.now(timezone.utc)
            job.current_size, job.progress = 0, 0
            db.commit()

            conn = job.remote_connection
            async with httpx.AsyncClient() as client:
                await self._send_chunks(job, conn, db, client)

                # Finalize
                verify_response = await client.post(
                    f"{conn.url.rstrip('/')}/api/remote/verify-transfer",
                    headers={"X-Remote-ID": my_url, "X-Shared-Secret": conn.shared_secret},
                    json={
                        "job_id": job.id,
                        "checksum": job.checksum,
                        "relative_path": job.relative_path,
                        "remote_path_id": job.remote_monitored_path_id,
                    },
                    timeout=30.0,
                )
                verify_response.raise_for_status()

                job.status = TransferStatus.COMPLETED
                job.end_time = datetime.now(timezone.utc)
                db.commit()

                # Cleanup
                await self._cleanup_after_transfer(db, job, conn)

        except Exception as e:
            logger.exception("Transfer failed for job %s", job_id)
            job.status = TransferStatus.FAILED
            job.error_message = str(e)
            job.retry_count += 1
            db.commit()
        finally:
            db.close()

    async def _cleanup_after_transfer(self, db, job, conn):
        """Remove original file and update inventory."""
        logger.info("Transfer completed for %s. Removing original file.", job.source_path)
        try:
            await anyio.to_thread.run_sync(Path(job.source_path).unlink)
            file_item = (
                db.query(FileInventory).filter(FileInventory.id == job.file_inventory_id).first()
            )
            if file_item:
                file_item.status = FileStatus.DELETED
                db.commit()
                audit_trail_service.log_remote_migration(
                    db=db,
                    file=file_item,
                    remote_url=conn.url,
                    success=True,
                    initiated_by="system",
                )
        except Exception:
            logger.exception("Failed to remove source file after transfer")


remote_transfer_service = RemoteTransferService()
