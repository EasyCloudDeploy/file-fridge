# ruff: noqa: B008, PLR0913
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import List

import aiofiles
import anyio
import httpx
import zstandard as zstd
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
)
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MonitoredPath, RemoteConnection, RemoteTransferJob, TransferStatus
from app.models import (
    FileInventory,
    FileStatus,
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    TransferDirection,
    TransferMode,
    TrustStatus,
)
from app.schemas import (
    ConnectionCodeResponse,
    PullTransferRequest,
    RemoteConnectionCreate,
    RemoteConnectionIdentity,
    RemoteConnectionUpdate,
    RemoteTransferJobBase,
)
from app.schemas import RemoteConnection as RemoteConnectionSchema
from app.schemas import RemoteTransferJob as RemoteTransferJobSchema
from app.security import PermissionChecker, get_current_user
from app.services.identity_service import identity_service
from app.services.instance_config_service import instance_config_service
from app.services.remote_connection_service import remote_connection_service
from app.services.remote_transfer_service import (
    get_transfer_timeouts,
    remote_transfer_service,
)
from app.services.scheduler import scheduler_service
from app.utils.disk_validator import disk_space_validator
from app.utils.remote_auth import remote_auth
from app.utils.remote_signature import (
    get_signed_headers,
    verify_remote_signature,
    verify_signature_from_components,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/remote", tags=["Remote Connections"])
INVALID_PATH_MSG = "Invalid relative path"


# Helper functions that were previously in this file
def _get_base_directory(path: MonitoredPath, storage_type: str) -> str:
    """Determine base directory for file storage."""
    if storage_type == "hot":
        logger.debug(f"Using hot storage path: {path.source_path}")
        return path.source_path
    if not path.storage_locations:
        error_msg = (
            f"No cold storage locations configured for path '{path.name}' (ID: {path.id}). "
            "Please configure at least one cold storage location for this monitored path."
        )
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    cold_path = path.storage_locations[0].path
    logger.debug(f"Using cold storage path: {cold_path}")
    return cold_path


async def _validate_and_build_path(base_dir: str, relative_path: str) -> Path:
    """Validate and build safe file path, preventing directory traversal."""
    safe_rel_path = Path(relative_path)
    if safe_rel_path.is_absolute():
        safe_rel_path = safe_rel_path.relative_to(safe_rel_path.anchor)
    if ".." in safe_rel_path.parts:
        raise HTTPException(status_code=400, detail=INVALID_PATH_MSG)
    tmp_path = (Path(base_dir) / safe_rel_path).absolute()
    try:
        is_safe = await anyio.to_thread.run_sync(
            lambda: tmp_path.resolve().is_relative_to(Path(base_dir).resolve())
        )
    except Exception:
        raise HTTPException(status_code=400, detail=INVALID_PATH_MSG) from None
    if not is_safe:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return tmp_path.with_suffix(tmp_path.suffix + ".fftmp")


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
            await anyio.to_thread.run_sync(lambda: found_tmp.unlink(missing_ok=True))
    except Exception:
        logger.exception(f"Error during checksum verification for {found_tmp}")
        await anyio.to_thread.run_sync(lambda: found_tmp.unlink(missing_ok=True))


async def _decrypt_chunk(
    chunk: bytes,
    nonce_hex: str,
    ephemeral_public_key_b64: str,
    remote_conn: RemoteConnection,
    db: Session,
) -> bytes:
    """
    Decrypt a file chunk using ECDH-derived key.

    Args:
        chunk: Encrypted chunk bytes
        nonce_hex: Hex-encoded GCM nonce
        ephemeral_public_key_b64: Sender's ephemeral X25519 public key (base64)
        remote_conn: RemoteConnection for ECDH with static key
        db: Database session to access our X25519 private key

    Returns:
        Decrypted chunk bytes
    """
    if not nonce_hex or not ephemeral_public_key_b64:
        # Not encrypted, return as-is
        return chunk

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    # 1. Load sender's ephemeral public key
    ephemeral_pub_bytes = base64.b64decode(ephemeral_public_key_b64)
    sender_ephemeral_public_key = x25519.X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)

    # 2. Get our X25519 private key
    our_private_key = identity_service.get_kx_private_key(db)

    # 3. Perform ECDH
    shared_secret = our_private_key.exchange(sender_ephemeral_public_key)

    # 4. Derive symmetric key using HKDF
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,  # AES-256
        salt=None,
        info=b"file-fridge-transfer-key",
    ).derive(shared_secret)

    # 5. Decrypt using AES-256-GCM
    aesgcm = AESGCM(derived_key)
    nonce = bytes.fromhex(nonce_hex)

    try:
        plaintext = aesgcm.decrypt(nonce, chunk, associated_data=None)
        return plaintext
    except Exception as e:
        logger.error(f"Chunk decryption failed: {e}")
        raise HTTPException(
            status_code=400, detail="Chunk decryption failed - invalid encryption or corrupted data"
        ) from None


async def _decompress_chunk(chunk: bytes) -> bytes:
    """Decompress chunk using zstandard."""
    try:
        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(chunk)
    except Exception:
        logger.exception("Decompression failed")
        raise HTTPException(status_code=400, detail="Decompression failed") from None


# --- New Handshake and Connection Endpoints ---


@router.get("/status", tags=["Remote Connections"])
def get_remote_status(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Check if remote connections are properly configured."""
    _ = current_user
    instance_url = instance_config_service.get_instance_url(db)
    is_configured = bool(instance_url)
    return {
        "configured": is_configured,
        "instance_url": instance_url if is_configured else None,
        "message": (
            "Remote connections are ready to use."
            if is_configured
            else "Remote connections require instance URL to be configured. "
            "Set the FF_INSTANCE_URL environment variable or configure it in the UI below."
        ),
    }


@router.get("/config", tags=["Remote Connections"])
def get_instance_config(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Get instance configuration including source information (environment vs database)."""
    _ = current_user
    return instance_config_service.get_config_info(db)


@router.post("/config", tags=["Remote Connections"])
def update_instance_config(
    config_data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """
    Update instance configuration (database values).

    Note: Environment variables take precedence and cannot be overridden via API.
    Only updates database values which serve as fallback.
    """
    _ = current_user

    # Check if values are set via environment variables
    if "instance_url" in config_data:
        instance_config_service.set_instance_url(db, config_data["instance_url"])

    if "instance_name" in config_data:
        instance_config_service.set_instance_name(db, config_data["instance_name"])

    return instance_config_service.get_config_info(db)


@router.get("/identity", response_model=RemoteConnectionIdentity, tags=["Remote Connections"])
def get_public_identity(db: Session = Depends(get_db)):
    """Return the public identity of this File Fridge instance."""
    instance_url = instance_config_service.get_instance_url(db)
    if not instance_url:
        logger.error("Instance URL not configured")
        raise HTTPException(
            status_code=500,
            detail="Instance URL not configured. Please set FF_INSTANCE_URL environment variable "
            "or configure it via the UI to enable remote connections.",
        )
    instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
    return {
        "instance_name": instance_name,
        "fingerprint": identity_service.get_instance_fingerprint(db),
        "ed25519_public_key": identity_service.get_signing_public_key_str(db),
        "x25519_public_key": identity_service.get_kx_public_key_str(db),
        "url": instance_url,
    }


@router.get("/my-identity", tags=["Remote Connections"])
def get_my_identity(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
def get_my_identity(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """
    Get this instance's identity information for sharing with others.
    Users share this fingerprint out-of-band to allow remote instances to verify.
    """
    _ = current_user
    instance_url = instance_config_service.get_instance_url(db)
    if not instance_url:
        raise HTTPException(
            status_code=500,
            detail="Instance URL not configured. Please set FF_INSTANCE_URL environment variable "
            "or configure it via the UI to enable remote connections.",
        )
    instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
    fingerprint = identity_service.get_instance_fingerprint(db)

    return {
        "instance_name": instance_name,
        "fingerprint": fingerprint,
        "url": instance_url,
        "qr_data": f"filefridge://{fingerprint}@{instance_url}",
    }


@router.get("/connection-code", response_model=ConnectionCodeResponse, tags=["Remote Connections"])
def get_connection_code(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    """
    Get the current rotating connection code for this instance.
    This code can be shared with other File Fridge instances to establish a connection.
    The code rotates automatically every hour for security.
    """
    _ = current_user
    instance_url = instance_config_service.get_instance_url(db)
    if not instance_url:
        raise HTTPException(
            status_code=500,
            detail="Instance URL not configured. Please set FF_INSTANCE_URL environment variable "
            "or configure it via the UI to enable remote connections.",
        )
    code, expires_in_seconds = remote_auth.get_code_with_expiry()
    return {"code": code, "expires_in_seconds": expires_in_seconds}


@router.post(
    "/connections/fetch-identity",
    response_model=RemoteConnectionIdentity,
    tags=["Remote Connections"],
)
async def fetch_remote_identity(
    data: RemoteConnectionCreate,
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Fetch the public identity of a remote instance to initiate a connection."""
    _ = current_user
    try:
        return await remote_connection_service.get_remote_identity(data.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/connect", response_model=RemoteConnectionSchema, tags=["Remote Connections"])
async def connect_with_code(
    connection_data: RemoteConnectionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """
    Establish a connection using a connection code.
    This endpoint fetches the remote instance's identity and creates a connection,
    including the connection code in the handshake for the remote to verify.
    The code verification happens server-side during the authenticated handshake,
    avoiding TOCTOU issues and authentication problems.
    """
    _ = current_user

    try:
        # Step 1: Fetch the remote identity
        remote_identity = await remote_connection_service.get_remote_identity(connection_data.url)

        # Step 2: Create the connection and send the code for verification
        # The connection code will be verified by the remote during the handshake
        return await remote_connection_service.initiate_connection(
            db, connection_data.name, remote_identity, connection_data.connection_code
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error connecting to remote instance")
        raise HTTPException(
            status_code=500,
            detail="Unexpected internal server error while processing remote request",
        ) from e


@router.post("/connections", response_model=RemoteConnectionSchema, tags=["Remote Connections"])
async def create_connection(
    name: str,
    remote_identity: RemoteConnectionIdentity,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Establish a trusted connection with a remote instance after verifying its identity."""
    _ = current_user
    try:
        return await remote_connection_service.initiate_connection(db, name, remote_identity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/connection-request", tags=["Remote Connections"])
async def handle_connection_request(request: Request, db: Session = Depends(get_db)):
    """Handles an incoming connection request from a remote instance (unauthenticated)."""
    try:
        request_data = await request.json()
        return remote_connection_service.handle_connection_request(db, request_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/connections", response_model=List[RemoteConnectionSchema], tags=["Remote Connections"]
)
def list_connections(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """List all remote connections."""
    _ = current_user
    return remote_connection_service.list_connections(db)


@router.delete("/connections/{connection_id}", tags=["Remote Connections"])
async def delete_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Delete a remote connection."""
    _ = current_user
    try:
        await remote_connection_service.delete_connection(db, connection_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "success"}


@router.post(
    "/connections/{connection_id}/trust",
    response_model=RemoteConnectionSchema,
    tags=["Remote Connections"],
)
def trust_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Manually trust a PENDING remote connection."""
    _ = current_user
    try:
        return remote_connection_service.trust_connection(db, connection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch(
    "/connections/{connection_id}",
    response_model=RemoteConnectionSchema,
    tags=["Remote Connections"],
)
async def update_connection(
    connection_id: int,
    update_data: RemoteConnectionUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Update a remote connection's name and/or transfer mode."""
    _ = current_user
    conn = remote_connection_service.get_connection(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    changed = False
    if update_data.name is not None:
        conn.name = update_data.name
        changed = True
    if update_data.transfer_mode is not None:
        conn.transfer_mode = update_data.transfer_mode
        changed = True

    if changed:
        db.commit()
        db.refresh(conn)

    # Notify remote of transfer mode change if connection is trusted
    if update_data.transfer_mode is not None and conn.trust_status == TrustStatus.TRUSTED:
        try:
            await remote_connection_service.notify_transfer_mode_change(db, conn)
        except Exception:
            logger.warning(
                "Failed to notify remote instance of transfer mode change for connection %s",
                connection_id,
            )

    return conn


# --- Endpoints Requiring Signature Verification ---


@router.post("/terminate-connection", tags=["Remote Connections"])
async def terminate_connection(
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
    db: Session = Depends(get_db),
):
    """Handle an incoming termination request."""
    remote_connection_service.handle_terminate_connection(db, remote_conn.remote_fingerprint)
    return {"status": "success"}


@router.get(
    "/connections/{connection_id}/paths",
    tags=["Remote Connections"],
)
async def get_remote_paths(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Fetch available MonitoredPaths from a remote instance."""
    _ = current_user
    conn = remote_connection_service.get_connection(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    async with httpx.AsyncClient() as client:
        try:
            url = f"{conn.url.rstrip('/')}/api/v1/remote/exposed-paths"
            headers = await get_signed_headers(db, "GET", url, b"")
            response = await client.get(url, headers=headers, timeout=get_transfer_timeouts())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.exception("Failed to fetch paths from remote")
            raise HTTPException(status_code=500, detail="Failed to fetch paths from remote") from e


@router.post("/migrate", response_model=RemoteTransferJobSchema, tags=["Remote Connections"])
async def migrate_file(
    migration_data: RemoteTransferJobBase,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Trigger a file migration to a remote instance."""
    _ = current_user
    logger.info(
        f"Migration requested: file_id={migration_data.file_inventory_id}, "
        f"remote_connection_id={migration_data.remote_connection_id}, "
        f"remote_path_id={migration_data.remote_monitored_path_id}"
    )
    try:
        job = remote_transfer_service.create_transfer_job(
            db,
            migration_data.file_inventory_id,
            migration_data.remote_connection_id,
            migration_data.remote_monitored_path_id,
        )
        logger.info(f"Transfer job {job.id} created successfully")
        return job
    except ValueError as e:
        logger.error(f"Failed to create transfer job: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/transfers", response_model=List[RemoteTransferJobSchema], tags=["Remote Connections"]
)
def list_transfers(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
@router.get("/transfers", response_model=List[RemoteTransferJobSchema], tags=["Remote Connections"])
def list_transfers(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """List all remote transfer jobs."""
    _ = current_user
    return db.query(RemoteTransferJob).order_by(RemoteTransferJob.id.desc()).all()


@router.post("/transfers/{job_id}/cancel", tags=["Remote Connections"])
def cancel_transfer(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Cancel a remote transfer job."""
    _ = current_user

    # Check if job exists first
    job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Transfer job {job_id} not found")

    # Check if job can be cancelled (must be PENDING or IN_PROGRESS)
    from app.models import TransferStatus

    if job.status not in (TransferStatus.PENDING, TransferStatus.IN_PROGRESS):
        raise HTTPException(
            status_code=400,
            detail=f"Transfer job {job_id} cannot be cancelled (current status: {job.status.value}). Only pending or in-progress transfers can be cancelled.",
        )

    success = remote_transfer_service.cancel_transfer(db, job_id)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to cancel transfer job {job_id}")
    return {"status": "success", "message": f"Transfer {job_id} cancelled"}


@router.post("/transfers/bulk/cancel", tags=["Remote Connections"])
def bulk_cancel_transfers(
    job_ids: List[int],
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Cancel multiple remote transfer jobs."""
    _ = current_user
    results = {"succeeded": [], "failed": []}
    for job_id in job_ids:
        if remote_transfer_service.cancel_transfer(db, job_id):
            results["succeeded"].append(job_id)
        else:
            results["failed"].append(job_id)
    return results


@router.delete("/transfers/{job_id}", tags=["Remote Connections"])
def delete_transfer(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Delete a transfer job record (for failed/completed/cancelled transfers)."""
    _ = current_user

    job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Transfer job {job_id} not found")

    # Only allow deletion of terminal state jobs
    from app.models import TransferStatus

    if job.status in (TransferStatus.PENDING, TransferStatus.IN_PROGRESS):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete transfer job {job_id} while it is {job.status.value}. Cancel it first or wait for it to complete.",
        )

    db.delete(job)
    db.commit()
    return {"status": "success", "message": f"Transfer job {job_id} deleted"}


@router.post("/transfers/bulk/delete", tags=["Remote Connections"])
def bulk_delete_transfers(
    job_ids: List[int],
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("Remote Connections")),
):
    """Delete multiple transfer job records."""
    _ = current_user

    results = {"succeeded": [], "failed": []}
    for job_id in job_ids:
        job = db.query(RemoteTransferJob).filter(RemoteTransferJob.id == job_id).first()
        if not job:
            results["failed"].append({"id": job_id, "reason": "not found"})
            continue

        if job.status in (TransferStatus.PENDING, TransferStatus.IN_PROGRESS):
            results["failed"].append({"id": job_id, "reason": "still in progress"})
            continue

        db.delete(job)
        results["succeeded"].append(job_id)

    db.commit()
    return results


class ReceiveHeader:
    def __init__(
        self,
        x_chunk_index: int = Header(..., alias="X-Chunk-Index"),
        x_relative_path: str = Header(..., alias="X-Relative-Path"),
        x_remote_path_id: int = Header(..., alias="X-Remote-Path-ID"),
        x_storage_type: str = Header(..., alias="X-Storage-Type"),
        x_encryption_nonce: str = Header("", alias="X-Encryption-Nonce"),
        x_ephemeral_public_key: str = Header("", alias="X-Ephemeral-Public-Key"),
        x_job_id: str = Header(..., alias="X-Job-ID"),
        x_is_final: bool = Header(..., alias="X-Is-Final"),
        x_fingerprint: str = Header(..., alias="X-Fingerprint"),
        x_timestamp: str = Header(..., alias="X-Timestamp"),
        x_nonce: str = Header(..., alias="X-Nonce"),
        x_signature: str = Header(..., alias="X-Signature"),
    ):
        self.chunk_index = x_chunk_index
        self.relative_path = x_relative_path
        self.remote_path_id = x_remote_path_id
        self.storage_type = x_storage_type
        self.encryption_nonce = x_encryption_nonce
        self.ephemeral_public_key = x_ephemeral_public_key
        self.job_id = x_job_id
        self.is_final = x_is_final
        self.fingerprint = x_fingerprint
        self.timestamp = x_timestamp
        self.nonce = x_nonce
        self.signature = x_signature


@router.post("/receive", tags=["Remote Connections"])
async def receive_chunk(
    request: Request,
    headers: ReceiveHeader = Depends(ReceiveHeader),
    db: Session = Depends(get_db),
):
    """Inter-instance endpoint to receive file chunks."""
    logger.info(
        f"Receiving chunk {headers.chunk_index} for job {headers.job_id}: "
        f"path_id={headers.remote_path_id}, relative_path={headers.relative_path}, "
        f"storage_type={headers.storage_type}, is_final={headers.is_final}"
    )

    body = await request.body()
    logger.debug(f"Chunk {headers.chunk_index} body size: {len(body)} bytes")

    try:
        remote_conn = await verify_signature_from_components(
            db,
            headers.fingerprint,
            headers.timestamp,
            headers.signature,
            headers.nonce,
            request,
            body,
        )
        logger.debug(f"Signature verified for chunk {headers.chunk_index} from {remote_conn.name}")
    except Exception as e:
        logger.error(f"Signature verification failed for chunk {headers.chunk_index}: {e}")
        raise

    path = db.query(MonitoredPath).filter(MonitoredPath.id == headers.remote_path_id).first()
    if not path:
        logger.error(
            f"MonitoredPath {headers.remote_path_id} not found for chunk {headers.chunk_index}"
        )
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    try:
        base_dir = _get_base_directory(path, headers.storage_type)
        logger.debug(f"Base directory for chunk {headers.chunk_index}: {base_dir}")
    except Exception as e:
        logger.error(
            f"Failed to get base directory for path {headers.remote_path_id}, "
            f"storage_type={headers.storage_type}: {e}"
        )
        raise

    try:
        tmp_path = await _validate_and_build_path(base_dir, headers.relative_path)
        logger.debug(f"Validated temp path for chunk {headers.chunk_index}: {tmp_path}")
    except HTTPException as e:
        logger.error(
            f"Path validation failed for chunk {headers.chunk_index}: "
            f"base_dir={base_dir}, relative_path={headers.relative_path}, error={e.detail}"
        )
        raise

    if headers.chunk_index == 0:
        file_size = (
            int(request.headers.get("X-File-Size", "0")) if "X-File-Size" in request.headers else 0
        )
        logger.info(f"First chunk for job {headers.job_id}, file size: {file_size} bytes")
        if file_size > 0:
            try:
                disk_space_validator.validate_disk_space_direct(file_size, Path(base_dir))
                logger.debug(f"Disk space validated for {file_size} bytes at {base_dir}")
            except ValueError as e:
                logger.error(f"Insufficient disk space for job {headers.job_id}: {e}")
                raise HTTPException(status_code=507, detail=str(e)) from None

    await anyio.to_thread.run_sync(lambda: tmp_path.parent.mkdir(parents=True, exist_ok=True))
    logger.debug(f"Created parent directory for chunk {headers.chunk_index}")

    try:
        decrypted_chunk = await _decrypt_chunk(
            body, headers.encryption_nonce, headers.ephemeral_public_key, remote_conn, db
        )
        logger.debug(
            f"Decrypted chunk {headers.chunk_index} "
            f"(encrypted: {len(body)} -> decrypted: {len(decrypted_chunk)} bytes)"
        )
    except Exception as e:
        logger.error(f"Decryption failed for chunk {headers.chunk_index}: {e}")
        raise

    try:
        decompressed_chunk = await _decompress_chunk(decrypted_chunk)
        logger.debug(
            f"Decompressed chunk {headers.chunk_index} "
            f"(compressed: {len(decrypted_chunk)} -> decompressed: {len(decompressed_chunk)} bytes)"
        )
    except Exception as e:
        logger.error(f"Decompression failed for chunk {headers.chunk_index}: {e}")
        raise

    mode = "ab" if headers.chunk_index > 0 else "wb"
    async with aiofiles.open(tmp_path, mode) as f:
        await f.write(decompressed_chunk)

    logger.info(
        f"Successfully received chunk {headers.chunk_index} for job {headers.job_id} "
        f"({len(decompressed_chunk)} bytes written)"
    )
    return {"status": "success", "chunk": headers.chunk_index}


@router.post("/verify-transfer", tags=["Remote Connections"])
async def verify_transfer(
    data: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """Finalize and verify a file transfer."""
    _ = remote_conn
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


@router.get("/transfer-status", tags=["Remote Connections"])
async def get_transfer_status(
    relative_path: str,
    remote_path_id: int,
    storage_type: str,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """Check the status of a file transfer on the remote instance."""
    _ = remote_conn
    path = db.query(MonitoredPath).filter(MonitoredPath.id == remote_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    base_dir = _get_base_directory(path, storage_type)
    final_path = (Path(base_dir) / relative_path).absolute()
    # Security check is implicitly handled by _validate_and_build_path logic if we reuse it
    if await anyio.to_thread.run_sync(final_path.exists):
        stat = await anyio.to_thread.run_sync(final_path.stat)
        return {"size": stat.st_size, "status": "completed"}

    tmp_path = final_path.with_suffix(final_path.suffix + ".fftmp")
    if await anyio.to_thread.run_sync(tmp_path.exists):
        stat = await anyio.to_thread.run_sync(tmp_path.stat)
        return {"size": stat.st_size, "status": "partial"}

    return {"size": 0, "status": "not_found"}


@router.get("/exposed-paths", tags=["Remote Connections"])
def get_exposed_paths(
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """Return MonitoredPaths for inter-instance selection."""
    _ = remote_conn
    paths = db.query(MonitoredPath).filter(MonitoredPath.enabled).all()
    return [{"id": p.id, "name": p.name} for p in paths]


# --- Bidirectional Transfer Endpoints ---


def _get_relative_path(file_obj: FileInventory, monitored_path: MonitoredPath) -> str:
    """Compute a relative path for *file_obj* against the monitored path.

    Tries, in order:
    1. ``relative_to(monitored_path.source_path)``
    2. ``relative_to(loc.path)`` for each cold-storage location
    3. Falls back to the bare filename
    """
    fp = Path(file_obj.file_path)
    try:
        return str(fp.relative_to(monitored_path.source_path))
    except ValueError:
        pass
    for loc in monitored_path.storage_locations:
        try:
            return str(fp.relative_to(loc.path))
        except ValueError:
            continue
    return fp.name


@router.get("/browse-files", tags=["Remote Connections"])
def browse_remote_files(
    path_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """
    Expose file inventory for a MonitoredPath to an authorized remote instance.
    Only accessible if the connection is effectively bidirectional.
    """
    if not remote_conn.effective_bidirectional:
        raise HTTPException(
            status_code=403,
            detail="Bidirectional mode not enabled. Both sides must enable it.",
        )

    path = (
        db.query(MonitoredPath).filter(MonitoredPath.id == path_id, MonitoredPath.enabled).first()
    )
    if not path:
        raise HTTPException(status_code=404, detail="MonitoredPath not found")

    query = db.query(FileInventory).filter(
        FileInventory.path_id == path_id,
        FileInventory.status == FileStatus.ACTIVE,
    )
    total = query.count()
    files = query.offset(skip).limit(limit).all()

    return {
        "path_name": path.name,
        "total_count": total,
        "files": [
            {
                "inventory_id": f.id,
                "file_path": f.file_path,
                "relative_path": _get_relative_path(f, path),
                "file_size": f.file_size or 0,
                "storage_type": f.storage_type.value if f.storage_type else "HOT",
                "file_mtime": f.file_mtime,
                "checksum": f.checksum,
            }
            for f in files
        ],
    }


@router.post("/serve-transfer", tags=["Remote Connections"])
async def serve_transfer_request(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """
    Accept a pull request from a remote instance.
    Creates a PULL transfer job on this (serving) side and starts
    sending chunks to the remote's /receive endpoint.
    """
    if not remote_conn.effective_bidirectional:
        raise HTTPException(
            status_code=403,
            detail="Bidirectional mode not enabled. Both sides must enable it.",
        )

    data = await request.json()
    file_inventory_id = data.get("file_inventory_id")
    remote_path_id = data.get("remote_monitored_path_id")

    if not file_inventory_id or not remote_path_id:
        raise HTTPException(
            status_code=400,
            detail="file_inventory_id and remote_monitored_path_id are required",
        )

    # Validate the file exists in our inventory
    file_obj = db.query(FileInventory).filter(FileInventory.id == file_inventory_id).first()
    if not file_obj:
        raise HTTPException(status_code=404, detail="File not found in inventory")

    try:
        job = remote_transfer_service.create_transfer_job(
            db,
            file_inventory_id,
            remote_conn.id,
            remote_path_id,
            direction=TransferDirection.PULL,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Queue the transfer to run in background
    background_tasks.add_task(remote_transfer_service.run_transfer, job.id)

    return {"status": "accepted", "job_id": job.id}


@router.post("/sync-transfer-mode", tags=["Remote Connections"])
def sync_transfer_mode(
    request: dict,
    db: Session = Depends(get_db),
    remote_conn: RemoteConnection = Depends(verify_remote_signature),
):
    """
    Receive a transfer mode update notification from a remote instance.
    Called when the remote changes its transfer_mode setting.
    """
    new_mode = request.get("transfer_mode")
    if new_mode not in [m.value for m in TransferMode]:
        raise HTTPException(status_code=400, detail="Invalid transfer mode")

    remote_conn.remote_transfer_mode = TransferMode(new_mode)
    db.commit()

    logger.info(
        "Updated remote_transfer_mode to %s for connection %s (effective_bidirectional=%s)",
        new_mode,
        remote_conn.name,
        remote_conn.effective_bidirectional,
    )

    return {
        "status": "success",
        "effective_bidirectional": remote_conn.effective_bidirectional,
    }


# --- Requesting-Side Endpoints (called by local admin to browse/pull) ---


@router.get(
    "/connections/{connection_id}/browse-files",
    tags=["Remote Connections"],
)
async def browse_remote_instance_files(
    connection_id: int,
    path_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Browse files on a remote instance for pull transfer."""
    _ = current_user
    conn = remote_connection_service.get_connection(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if not conn.effective_bidirectional:
        raise HTTPException(
            status_code=403,
            detail="Bidirectional mode not enabled. Both sides must enable it.",
        )

    base_url = f"{conn.url.rstrip('/')}/api/v1/remote/browse-files"
    params = {"path_id": str(path_id), "skip": str(skip), "limit": str(limit)}
    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signed_url = f"{base_url}?{query_string}"

    headers = await get_signed_headers(db, "GET", signed_url, b"")

    async with httpx.AsyncClient(timeout=get_transfer_timeouts()) as client:
        try:
            response = await client.get(base_url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Remote returned error browsing files: %s", e.response.text)
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Remote error: {e.response.text}",
            ) from e
        except Exception as e:
            logger.exception("Failed to browse files on remote instance")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to browse remote files: {e}",
            ) from e


@router.post("/pull", tags=["Remote Connections"])
async def pull_file(
    pull_data: PullTransferRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Request a file from a remote instance (pull transfer).
    Sends a serve-transfer request to the remote, which will then
    push chunks back to this instance's /receive endpoint.
    """
    _ = current_user
    conn = remote_connection_service.get_connection(db, pull_data.remote_connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if not conn.effective_bidirectional:
        raise HTTPException(
            status_code=403,
            detail="Bidirectional mode not enabled. Both sides must enable it.",
        )

    # Verify local destination path exists
    local_path = (
        db.query(MonitoredPath)
        .filter(MonitoredPath.id == pull_data.local_monitored_path_id)
        .first()
    )
    if not local_path:
        raise HTTPException(status_code=404, detail="Local MonitoredPath not found")

    url = f"{conn.url.rstrip('/')}/api/v1/remote/serve-transfer"
    payload = {
        "file_inventory_id": pull_data.remote_file_inventory_id,
        "remote_monitored_path_id": pull_data.local_monitored_path_id,
    }
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = await get_signed_headers(db, "POST", url, body_bytes)
    headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=get_transfer_timeouts()) as client:
        try:
            response = await client.post(url, headers=headers, content=body_bytes)
            response.raise_for_status()
            result = response.json()
            return {
                "status": "accepted",
                "remote_job_id": result.get("job_id"),
                "message": "Pull transfer initiated. The remote instance will send the file.",
            }
        except httpx.HTTPStatusError as e:
            logger.error("Remote returned error for pull request: %s", e.response.text)
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Remote error: {e.response.text}",
            ) from e
        except Exception as e:
            logger.exception("Failed to initiate pull transfer")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to request file from remote: {e}",
            ) from e
