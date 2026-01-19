# ruff: noqa: B008
import hashlib
import logging
import secrets
from pathlib import Path
from typing import List

import aiofiles
import anyio
import httpx
import zstandard as zstd
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import MonitoredPath, RemoteConnection, RemoteTransferJob, TransferStatus
from app.schemas import RemoteConnection as RemoteConnectionSchema
from app.schemas import RemoteConnectionCreate, RemoteTransferJobBase
from app.schemas import RemoteTransferJob as RemoteTransferJobSchema
from app.security import get_current_user
from app.services.remote_connection_service import remote_connection_service
from app.services.remote_transfer_service import remote_transfer_service
from app.services.scheduler import scheduler_service
from app.utils.disk_validator import disk_space_validator
from app.utils.rate_limiter import check_rate_limit
from app.utils.remote_auth import remote_auth
from app.utils.retry_strategy import retry_strategy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/remote", tags=["Remote Connections"])


def verify_remote_secret(
    x_remote_id: str = Header(..., alias="X-Remote-ID"),
    x_shared_secret: str = Header(..., alias="X-Shared-Secret"),
    db: Session = Depends(get_db),
):
    """Verify the shared secret for inter-instance communication.

    X-Remote-ID should be the base URL of the remote instance.
    """
    conn = db.query(RemoteConnection).filter(RemoteConnection.url == x_remote_id).first()
    if not conn or not secrets.compare_digest(conn.shared_secret, x_shared_secret):
        logger.warning(f"Unauthorized remote access attempt from ID: {x_remote_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid remote ID or shared secret"
        )
    return conn


@router.get("/connection-code")
def get_connection_code(current_user: dict = Depends(get_current_user)):
    """Get the current rotating connection code."""
    _ = current_user
    return {"code": remote_auth.get_code()}


@router.post("/connect", response_model=RemoteConnectionSchema)
async def connect(
    connection_data: RemoteConnectionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    """Initiate connection to another instance."""
    try:
        return await remote_connection_service.connect_to_remote(db, connection_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/handshake")
async def handshake(handshake_data: dict, db: Session = Depends(get_db)):
    """Inter-instance handshake endpoint."""
    try:
        remote_connection_service.handle_handshake(db, handshake_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "success"}


@router.get("/connections", response_model=List[RemoteConnectionSchema])
def list_connections(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """List all remote connections."""
    _ = current_user
    return remote_connection_service.list_connections(db)


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    """Delete a remote connection."""
    try:
        await remote_connection_service.delete_connection(db, connection_id, force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "success"}


@router.post("/terminate-connection")
async def terminate_connection(
    data: dict,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
):
    """Handle an incoming termination request."""
    _ = remote_conn
    remote_connection_service.handle_terminate_connection(db, data["url"])
    return {"status": "success"}


@router.get("/connections/{connection_id}/paths")
async def get_remote_paths(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    """Fetch available MonitoredPaths from a remote instance."""
    conn = remote_connection_service.get_connection(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    my_url = settings.ff_instance_url or "http://localhost:8000"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{conn.url.rstrip('/')}/api/remote/exposed-paths",
                headers={"X-Remote-ID": my_url, "X-Shared-Secret": conn.shared_secret},
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.exception("Failed to fetch paths from remote")
            raise HTTPException(status_code=500, detail="Failed to fetch paths from remote") from e


@router.post("/migrate", response_model=RemoteTransferJobSchema)
async def migrate_file(
    migration_data: RemoteTransferJobBase,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Trigger a file migration to a remote instance."""
    _ = current_user
    try:
        return remote_transfer_service.create_transfer_job(
            db,
            migration_data.file_inventory_id,
            migration_data.remote_connection_id,
            migration_data.remote_monitored_path_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/transfers", response_model=List[RemoteTransferJobSchema])
def list_transfers(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """List all remote transfer jobs."""
    _ = current_user
    return db.query(RemoteTransferJob).order_by(RemoteTransferJob.id.desc()).all()


@router.post("/transfers/{job_id}/cancel")
def cancel_transfer(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cancel a transfer job."""
    _ = current_user
    try:
        remote_transfer_service.cancel_transfer(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        return {"status": "success"}


@router.post("/transfers/bulk/cancel")
def bulk_cancel_transfers(
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cancel multiple transfer jobs."""
    _ = current_user
    job_ids = data.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")

    cancelled_count = 0
    errors = []

    for job_id in job_ids:
        try:
            remote_transfer_service.cancel_transfer(db, job_id)
            cancelled_count += 1
        except ValueError as e:
            errors.append({"job_id": job_id, "error": str(e)})

    return {
        "status": "success",
        "cancelled_count": cancelled_count,
        "error_count": len(errors),
        "errors": errors,
    }


@router.post("/transfers/bulk/retry")
def bulk_retry_transfers(
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retry failed transfers by resetting them to PENDING."""
    _ = current_user
    job_ids = data.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")

    from app.utils.retry_strategy import MAX_RETRIES

    # Get failed jobs and reset them to PENDING
    jobs = (
        db.query(RemoteTransferJob)
        .filter(RemoteTransferJob.id.in_(job_ids))
        .filter(RemoteTransferJob.status == TransferStatus.FAILED)
        .with_for_update()
        .all()
    )

    retried_count = 0
    skipped_count = 0

    for job in jobs:
        if job.retry_count >= retry_strategy.max_retries:
            skipped_count += 1
            logger.warning(f"Skipping transfer {job.id}: exceeded max retries")
        else:
            job.status = TransferStatus.PENDING
            job.error_message = None
            job.retry_count = 0
            job.start_time = None
            job.end_time = None
            retried_count += 1
            logger.info(f"Retrying failed transfer {job.id}")

    db.commit()

    return {
        "status": "success",
        "retried_count": retried_count,
        "skipped_count": skipped_count,
    }


class ReceiveHeader:
    def __init__(  # noqa: PLR0913
        self,
        x_chunk_index: int = Header(..., alias="X-Chunk-Index"),
        x_relative_path: str = Header(..., alias="X-Relative-Path"),
        x_remote_path_id: int = Header(..., alias="X-Remote-Path-ID"),
        x_storage_type: str = Header(..., alias="X-Storage-Type"),
        x_nonce: str = Header("", alias="X-Nonce"),
        x_job_id: str = Header(..., alias="X-Job-ID"),
        x_is_final: bool = Header(..., alias="X-Is-Final"),
    ):
        self.chunk_index = x_chunk_index
        self.relative_path = x_relative_path
        self.remote_path_id = x_remote_path_id
        self.storage_type = x_storage_type
        self.nonce = x_nonce
        self.job_id = x_job_id
        self.is_final = x_is_final


def _get_base_directory(path: MonitoredPath, storage_type: str) -> str:
    """Determine base directory for file storage."""
    if storage_type == "hot":
        return path.source_path
    # For cold, use the first storage location
    if not path.storage_locations:
        raise HTTPException(status_code=400, detail="No cold storage locations configured")
    return path.storage_locations[0].path


INVALID_PATH_MSG = "Invalid relative path"


async def _validate_and_build_path(base_dir: str, relative_path: str) -> Path:
    """Validate and build safe file path, preventing directory traversal."""
    # Normalize relative path to prevent directory traversal
    safe_rel_path = Path(relative_path)
    if safe_rel_path.is_absolute():
        safe_rel_path = safe_rel_path.relative_to(safe_rel_path.anchor)

    # Explicitly prevent upward directory traversal
    if ".." in safe_rel_path.parts:
        raise HTTPException(status_code=400, detail=INVALID_PATH_MSG)

    tmp_path = (Path(base_dir) / safe_rel_path).absolute()

    # SECURITY: Ensure the resolved path is within the base directory
    try:
        is_safe = await anyio.to_thread.run_sync(
            lambda: tmp_path.resolve().is_relative_to(Path(base_dir).resolve())
        )
    except Exception:
        raise HTTPException(status_code=400, detail=INVALID_PATH_MSG) from None

    if not is_safe:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    return tmp_path.with_suffix(tmp_path.suffix + ".fftmp")


async def _decrypt_chunk(chunk: bytes, nonce: str, shared_secret: str) -> bytes:
    """Decrypt chunk if nonce is provided."""
    if not nonce:
        return chunk

    try:
        key = bytes.fromhex(shared_secret)[:32]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(bytes.fromhex(nonce), chunk, None)
    except Exception:
        logger.exception("Decryption failed")
        raise HTTPException(status_code=400, detail="Decryption failed") from None


async def _decompress_chunk(chunk: bytes) -> bytes:
    """Decompress chunk using zstandard."""
    try:
        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(chunk)
    except Exception:
        logger.exception("Decompression failed")
        raise HTTPException(status_code=400, detail="Decompression failed") from None


@router.post("/receive")
async def receive_chunk(
    request: Request,
    headers: ReceiveHeader = Depends(ReceiveHeader),
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
):
    """Inter-instance endpoint to receive file chunks."""
    _ = remote_conn
    check_rate_limit(request)

    # 1. Get MonitoredPath
    path = db.query(MonitoredPath).filter(MonitoredPath.id == headers.remote_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    # 2. Determine base directory and build safe path
    base_dir = _get_base_directory(path, headers.storage_type)
    tmp_path = await _validate_and_build_path(base_dir, headers.relative_path)

    # 3. Validate disk space on first chunk
    if headers.chunk_index == 0:
        file_size = (
            int(request.headers.get("X-File-Size", "0")) if "X-File-Size" in request.headers else 0
        )
        if file_size > 0:
            try:
                disk_space_validator.validate_disk_space_direct(file_size, Path(base_dir))
            except ValueError as e:
                logger.warning(f"Insufficient disk space for transfer: {e}")
                raise HTTPException(status_code=507, detail=str(e)) from None

    # 3. Ensure directory exists
    await anyio.to_thread.run_sync(lambda: tmp_path.parent.mkdir(parents=True, exist_ok=True))

    # 4. Get, decrypt, and decompress body
    body = await request.body()
    decrypted_chunk = await _decrypt_chunk(body, headers.nonce, remote_conn.shared_secret)
    decompressed_chunk = await _decompress_chunk(decrypted_chunk)

    # 5. Write chunk
    mode = "ab" if headers.chunk_index > 0 else "wb"
    async with aiofiles.open(tmp_path, mode) as f:
        await f.write(decompressed_chunk)

    return {"status": "success", "chunk": headers.chunk_index}


async def _get_found_tmp(db: Session, remote_path_id: int, relative_path: str) -> Path:
    path = db.query(MonitoredPath).filter(MonitoredPath.id == remote_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    possible_dirs = [path.source_path]
    for loc in path.storage_locations:
        possible_dirs.append(loc.path)

    safe_rel_path = Path(relative_path)
    if safe_rel_path.is_absolute():
        safe_rel_path = safe_rel_path.relative_to(safe_rel_path.anchor)

    if ".." in safe_rel_path.parts:
        raise HTTPException(status_code=400, detail=INVALID_PATH_MSG)

    for d in possible_dirs:
        p = (Path(d) / safe_rel_path).absolute()
        try:
            is_relative = await anyio.to_thread.run_sync(
                lambda p=p, d=d: p.resolve().is_relative_to(Path(d).resolve())
            )
            if is_relative:
                p_tmp = p.with_suffix(p.suffix + ".fftmp")
                if await anyio.to_thread.run_sync(p_tmp.exists):
                    return p_tmp
        except Exception:
            continue

    raise HTTPException(status_code=404, detail="Temporary file not found")


async def _verify_checksum_in_background(found_tmp: Path, checksum: str):
    """Verify checksum in background and delete file if mismatch."""
    try:
        sha256_hash = hashlib.sha256()
        async with aiofiles.open(found_tmp, "rb") as f:
            while True:
                byte_block = await f.read(4096)
                if not byte_block:
                    break
                sha256_hash.update(byte_block)

        if sha256_hash.hexdigest() != checksum:
            logger.error(f"Checksum verification failed for {found_tmp}, deleting file")
            await anyio.to_thread.run_sync(found_tmp.unlink, missing_ok=True)
    except Exception:
        logger.exception(f"Error during checksum verification for {found_tmp}")
        await anyio.to_thread.run_sync(found_tmp.unlink, missing_ok=True)


@router.post("/verify-transfer")
async def verify_transfer(
    data: dict,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
):
    """Finalize and verify a file transfer."""
    _ = remote_conn
    check_rate_limit(request)
    relative_path = data["relative_path"]
    remote_path_id = data["remote_path_id"]
    checksum = data.get("checksum")

    found_tmp = await _get_found_tmp(db, remote_path_id, relative_path)

    if checksum:
        final_path = found_tmp.with_suffix("")
        await anyio.to_thread.run_sync(found_tmp.rename, final_path)
        background_tasks.add_task(_verify_checksum_in_background, final_path, checksum)
    else:
        final_path = found_tmp.with_suffix("")
        await anyio.to_thread.run_sync(found_tmp.rename, final_path)

    background_tasks.add_task(scheduler_service.trigger_scan, remote_path_id)
    return {"status": "success"}


@router.get("/exposed-paths")
def get_exposed_paths(
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
):
    """Return MonitoredPaths for inter-instance selection."""
    _ = remote_conn  # Unused, but required for authentication
    paths = db.query(MonitoredPath).filter(MonitoredPath.enabled).all()
    return [{"id": p.id, "name": p.name} for p in paths]
