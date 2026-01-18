import hashlib
import logging
import secrets
from pathlib import Path
from typing import List, Optional

import httpx
import zstandard as zstd
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MonitoredPath, RemoteConnection, RemoteTransferJob, StorageType
from app.schemas import RemoteConnection as RemoteConnectionSchema
from app.schemas import RemoteConnectionCreate, RemoteTransferJobBase
from app.schemas import RemoteTransferJob as RemoteTransferJobSchema
from app.security import get_current_user
from app.services.remote_connection_service import remote_connection_service
from app.services.remote_transfer_service import remote_transfer_service
from app.services.scheduler import scheduler_service
from app.utils.remote_auth import remote_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/remote", tags=["Remote Connections"])


async def verify_remote_secret(
    x_remote_id: str = Header(..., alias="X-Remote-ID"),
    x_shared_secret: str = Header(..., alias="X-Shared-Secret"),
    db: Session = Depends(get_db),
):
    """Verify the shared secret for inter-instance communication."""
    try:
        remote_id = int(x_remote_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Remote ID"
        ) from None

    conn = db.query(RemoteConnection).filter(RemoteConnection.id == remote_id).first()
    if not conn or not secrets.compare_digest(conn.shared_secret, x_shared_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shared secret"
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
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/handshake")
async def handshake(handshake_data: dict, db: Session = Depends(get_db)):
    """Inter-instance handshake endpoint."""
    try:
        remote_connection_service.handle_handshake(db, handshake_data)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{conn.url.rstrip('/')}/api/remote/exposed-paths",
                headers={"X-Shared-Secret": conn.shared_secret},
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.exception("Failed to fetch paths from remote")
            raise HTTPException(
                status_code=500, detail="Failed to fetch paths from remote"
            ) from None


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


@router.post("/receive")
async def receive_chunk(
    request: Request,
    x_chunk_index: int = Header(..., alias="X-Chunk-Index"),
    x_relative_path: str = Header(..., alias="X-Relative-Path"),
    x_remote_path_id: int = Header(..., alias="X-Remote-Path-ID"),
    x_storage_type: str = Header(..., alias="X-Storage-Type"),
    x_nonce: str = Header("", alias="X-Nonce"),
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
    x_job_id: str = Header(..., alias="X-Job-ID"),
    x_is_final: bool = Header(..., alias="X-Is-Final"),
):
    """Inter-instance endpoint to receive file chunks."""
    _ = x_job_id
    _ = x_is_final
    _ = remote_conn
    # 1. Get MonitoredPath
    path = db.query(MonitoredPath).filter(MonitoredPath.id == x_remote_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    # 2. Determine base directory
    if x_storage_type == "hot":
        base_dir = path.source_path
    else:
        # For cold, use the first storage location
        if not path.storage_locations:
            raise HTTPException(status_code=400, detail="No cold storage locations configured")
        base_dir = path.storage_locations[0].path

    # 3. Build full path
    # Normalize relative path to prevent directory traversal
    safe_rel_path = Path(x_relative_path)
    if safe_rel_path.is_absolute():
        safe_rel_path = safe_rel_path.relative_to(safe_rel_path.anchor)

    # Explicitly prevent upward directory traversal
    if ".." in safe_rel_path.parts:
        raise HTTPException(status_code=400, detail="Invalid relative path")

    tmp_path = (Path(base_dir) / safe_rel_path).absolute()

    # SECURITY: Ensure the resolved path is within the base directory
    try:
        is_safe = tmp_path.resolve().is_relative_to(Path(base_dir).resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid relative path") from None

    if not is_safe:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    tmp_path = tmp_path.with_suffix(tmp_path.suffix + ".fftmp")

    # Ensure directory exists
    tmp_path.parent.mkdir(parents=True, exist_ok=True)

    # 4. Get body
    body = await request.body()

    # 5. Decrypt if needed
    decrypted_chunk = body
    if x_nonce:
        try:
            key = bytes.fromhex(remote_conn.shared_secret)[:32]
            aesgcm = AESGCM(key)
            decrypted_chunk = aesgcm.decrypt(bytes.fromhex(x_nonce), body, None)
        except Exception:
            logger.exception("Decryption failed")
            raise HTTPException(status_code=400, detail="Decryption failed") from None

    # 6. Decompress
    try:
        dctx = zstd.ZstdDecompressor()
        decompressed_chunk = dctx.decompress(decrypted_chunk)
    except Exception:
        logger.exception("Decompression failed")
        raise HTTPException(status_code=400, detail="Decompression failed") from None

    # 7. Write chunk
    mode = "ab" if x_chunk_index > 0 else "wb"
    with tmp_path.open(mode) as f:
        f.write(decompressed_chunk)

    return {"status": "success", "chunk": x_chunk_index}


@router.post("/verify-transfer")
async def verify_transfer(
    data: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_secret),
):
    """Finalize and verify a file transfer."""
    _ = remote_conn  # Unused, but required for authentication
    relative_path = data["relative_path"]
    remote_path_id = data["remote_path_id"]
    checksum = data.get("checksum")

    path = db.query(MonitoredPath).filter(MonitoredPath.id == remote_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    # Search for the .fftmp file in possible locations
    possible_dirs = [path.source_path]
    for loc in path.storage_locations:
        possible_dirs.append(loc.path)

    safe_rel_path = Path(relative_path)
    if safe_rel_path.is_absolute():
        safe_rel_path = safe_rel_path.relative_to(safe_rel_path.anchor)

    # Explicitly prevent upward directory traversal
    if ".." in safe_rel_path.parts:
        raise HTTPException(status_code=400, detail="Invalid relative path")

    found_tmp = None
    for d in possible_dirs:
        p = (Path(d) / safe_rel_path).absolute()
        # SECURITY: Ensure it's within one of the allowed bases
        try:
            if p.resolve().is_relative_to(Path(d).resolve()):
                p_tmp = p.with_suffix(p.suffix + ".fftmp")
                if p_tmp.exists():
                    found_tmp = p_tmp
                    break
        except Exception:
            continue

    if not found_tmp:
        raise HTTPException(status_code=404, detail="Temporary file not found")

    # Verify checksum if provided
    if checksum:
        sha256_hash = hashlib.sha256()
        with found_tmp.open("rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)

        if sha256_hash.hexdigest() != checksum:
            raise HTTPException(status_code=400, detail="Checksum verification failed")

    # Move to final location
    final_path = found_tmp.with_suffix("")
    found_tmp.rename(final_path)

    # Trigger a scan for this path in the background
    background_tasks.add_task(scheduler_service.trigger_scan, path.id)

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
