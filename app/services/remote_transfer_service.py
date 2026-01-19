import asyncio
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
from app.utils.circuit_breaker import get_circuit_breaker
from app.utils.retry_strategy import retry_strategy

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1MB chunks
MAX_RETRIES = retry_strategy.max_retries


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
                .with_for_update()
                .all()
            )
            for job in jobs:
                # Refresh status in case it changed during lock acquisition
                db.refresh(job)
                if job.status == TransferStatus.CANCELLED:
                    continue
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
            "X-File-Size": str(job.total_size),
        }

    async def _get_remote_status(self, client, conn, job):
        """Check remote status for resumability."""
        my_url = settings.ff_instance_url or "http://localhost:8000"
        try:
            response = await client.get(
                f"{conn.url.rstrip('/')}/api/remote/transfer-status",
                params={
                    "relative_path": job.relative_path,
                    "remote_path_id": job.remote_monitored_path_id,
                    "storage_type": job.storage_type.value,
                },
                headers={"X-Remote-ID": my_url, "X-Shared-Secret": conn.shared_secret},
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug(f"Failed to get remote status for job {job.id}, starting fresh")
            return {"size": 0, "status": "not_found"}

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

        remote_status = await self._get_remote_status(client, conn, job)
        remote_size = remote_status.get("size", 0)

        async with aiofiles.open(source_path, "rb") as f:
            if remote_size > 0:
                if remote_size >= job.total_size:
                    logger.info(f"Remote file already complete for job {job.id}")
                    job.current_size = job.total_size
                    job.progress = 100
                    db.commit()
                    return

                await f.seek(remote_size)
                logger.info(f"Resuming transfer for job {job.id} from byte {remote_size}")

            # Calculate chunk_idx: if we have bytes, we are at least on chunk 1
            # (or higher). chunk_idx=0 always triggers 'wb' (truncate).
            chunk_idx = (remote_size // CHUNK_SIZE) if remote_size > 0 else 0
            if remote_size > 0 and chunk_idx == 0:
                chunk_idx = 1

            job.current_size = remote_size
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
            job = (
                db.query(RemoteTransferJob)
                .filter(RemoteTransferJob.id == job_id)
                .with_for_update()
                .first()
            )
            if not job:
                return

            # Double-check status after lock acquisition
            if job.status not in [TransferStatus.PENDING, TransferStatus.FAILED]:
                logger.info(f"Job {job_id} is {job.status.value}, skipping transfer")
                return

            # Validate source file exists and has disk space on remote (via header)
            source_path = Path(job.source_path)
            if not source_path.exists():
                logger.error(f"Source file does not exist: {source_path}")
                job.status = TransferStatus.FAILED
                job.error_message = "Source file not found"
                job.retry_count += 1
                job.end_time = datetime.now(timezone.utc)
                db.commit()
                return

            job.status = TransferStatus.IN_PROGRESS
            job.start_time = datetime.now(timezone.utc)
            job.current_size, job.progress = 0, 0
            db.commit()

            conn = job.remote_connection

            # Check circuit breaker
            circuit_breaker = get_circuit_breaker(conn.id)
            if not circuit_breaker.can_attempt():
                msg = (
                    f"Circuit breaker is open for connection {conn.id}, "
                    f"skipping transfer to {conn.url}"
                )
                logger.warning(msg)
                job.status = TransferStatus.FAILED
                job.error_message = (
                    "Remote instance is temporarily unavailable (circuit breaker open)"
                )
                job.end_time = datetime.now(timezone.utc)
                db.commit()
                return

            # Retry loop with exponential backoff
            for attempt in range(MAX_RETRIES):
                try:
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
                            timeout=300.0,
                        )
                        verify_response.raise_for_status()

                        job.status = TransferStatus.COMPLETED
                        job.end_time = datetime.now(timezone.utc)
                        db.commit()

                        # Cleanup
                        await self._cleanup_after_transfer(db, job, conn)
                        logger.info(f"Transfer {job_id} completed successfully")
                        circuit_breaker.record_success()
                        return

                except Exception as e:
                    circuit_breaker.record_failure()
                    error_type = retry_strategy.classify_error(e)
                    should_retry, delay, reason = retry_strategy.should_retry(attempt, e)

                    logger.warning(
                        f"Transfer {job_id} attempt {attempt + 1} failed: {reason}. "
                        f"Error type: {error_type.value}"
                    )

                    if not should_retry:
                        # Permanent error or max retries reached
                        job.status = TransferStatus.FAILED
                        job.error_message = f"{e!s} ({error_type.value})"
                        job.end_time = datetime.now(timezone.utc)
                        job.retry_count = attempt + 1
                        db.commit()
                        logger.exception(
                            f"Transfer {job_id} failed permanently: {job.error_message}"
                        )
                        return

                    # Transient error - wait before retry
                    job.error_message = f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {reason}"
                    db.commit()
                    logger.info(f"Waiting {delay:.1f}s before retry...")
                    await asyncio.sleep(delay)

            # Should not reach here, but just in case
            logger.error(f"Transfer {job_id} failed after {MAX_RETRIES} retries")
            job.status = TransferStatus.FAILED
            job.error_message = f"Failed after {MAX_RETRIES} retries"
            job.end_time = datetime.now(timezone.utc)
            job.retry_count = MAX_RETRIES
            db.commit()
        finally:
            db.close()

    def cancel_transfer(self, db: Session, job_id: int):
        """Cancel a transfer job."""
        job = (
            db.query(RemoteTransferJob)
            .filter(RemoteTransferJob.id == job_id)
            .with_for_update()
            .first()
        )
        if not job:
            msg = f"Transfer job {job_id} not found"
            raise ValueError(msg)

        if job.status in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
            msg = f"Cannot cancel transfer with status {job.status.value}"
            raise ValueError(msg)

        job.status = TransferStatus.CANCELLED
        job.end_time = datetime.now(timezone.utc)
        job.error_message = "Cancelled by user"
        db.commit()
        db.refresh(job)
        return job

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
