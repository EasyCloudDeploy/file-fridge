"""API routes for storage management."""

import base64
import hashlib
import hmac
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Annotated, List, Optional
from urllib.parse import quote as url_quote
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    ColdStorageBackendType,
    ColdStorageLocation,
    EncryptionStatus,
    FileInventory,
    FileRecord,
)
from app.schemas import ColdStorageLocation as ColdStorageLocationSchema
from app.schemas import (
    ColdStorageLocationCreate,
    ColdStorageLocationUpdate,
    ColdStorageLocationWithStats,
    StorageStats,
)
from app.services.cold_storage_backends import get_backend
from app.services.instance_config_service import instance_config_service
from app.services.scheduler import scheduler_service
from app.utils.db_utils import escape_like_string
from app.utils.local_drive_identity import update_local_drive_identity_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
public_router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


def _sanitize_backend_config(config: dict) -> dict:
    """Redact sensitive backend config values before returning API responses."""
    redacted = {}
    sensitive_markers = (
        "secret",
        "token",
        "password",
        "private_key",
        "access_key",
        "client_id",
    )
    for key, value in config.items():
        key_lower = str(key).lower()
        if any(marker in key_lower for marker in sensitive_markers):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _location_to_schema(location: ColdStorageLocation, path_count: Optional[int] = None):
    backend = get_backend(location)
    capabilities = backend.capabilities()
    raw_backend_config = location.get_backend_config()
    payload = {
        "id": location.id,
        "name": location.name,
        "path": location.path,
        "backend_type": location.backend_type,
        "operation_mode": location.operation_mode,
        "backend_config": _sanitize_backend_config(raw_backend_config),
        "backend_status": {
            "gdrive_connected": (
                bool(raw_backend_config.get("refresh_token"))
                if location.backend_type == ColdStorageBackendType.GDRIVE
                else None
            )
        },
        "backend_capabilities": {
            "supports_move": capabilities.supports_move,
            "supports_copy": capabilities.supports_copy,
            "supports_symlink": capabilities.supports_symlink,
            "supports_local_path_stats": capabilities.supports_local_path_stats,
        },
        "local_drive_identifier": location.local_drive_identifier,
        "local_drive_label": location.local_drive_label,
        "local_drive_mount_path": location.local_drive_mount_path,
        "local_drive_is_removable": location.local_drive_is_removable,
        "local_drive_is_connected": location.local_drive_is_connected,
        "local_drive_last_seen_at": location.local_drive_last_seen_at,
        "allow_offline": location.allow_offline,
        "paused": location.paused,
        "caution_threshold_percent": location.caution_threshold_percent,
        "critical_threshold_percent": location.critical_threshold_percent,
        "is_encrypted": location.is_encrypted,
        "encryption_status": location.encryption_status,
        "permissions_error": location.permissions_error,
        "created_at": location.created_at,
        "updated_at": location.updated_at,
    }
    if path_count is not None:
        payload["path_count"] = path_count
        return ColdStorageLocationWithStats(**payload)
    return ColdStorageLocationSchema(**payload)


def _can_tolerate_offline(location: ColdStorageLocation, validation_error: Optional[str]) -> bool:
    """Whether missing local storage should be tolerated for this location."""
    if location.backend_type != ColdStorageBackendType.LOCAL:
        return False
    if not location.allow_offline:
        return False
    if location.local_drive_is_removable and location.local_drive_is_connected is False:
        return True
    error_text = (validation_error or "").lower()
    return "not found" in error_text or "does not exist" in error_text


def _make_gdrive_state(location_id: int) -> str:
    if not (settings.secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server secret key is not configured; cannot create OAuth state token",
        )
    payload = {
        "location_id": location_id,
        "ts": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    secret = settings.secret_key.encode("utf-8")
    signature = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
    token = {
        "payload": payload,
        "sig": signature,
    }
    return base64.urlsafe_b64encode(
        json.dumps(token, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("utf-8")


def _parse_gdrive_state(state: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
        token = json.loads(decoded)
        payload = token["payload"]
        signature = token["sig"]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state"
        ) from exc

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if not (settings.secret_key or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    secret = settings.secret_key.encode("utf-8")
    expected = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth signature"
        )

    ts = int(payload.get("ts", 0))
    if abs(int(time.time()) - ts) > 600:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state expired")

    location_id = int(payload.get("location_id", 0))
    if location_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth location"
        )
    return location_id


def _get_google_oauth_redirect_uri(request: Request, db: Session) -> str:
    """Build a stable OAuth callback URL using configured public instance URL when available."""
    instance_url = instance_config_service.get_instance_url(db)
    if instance_url:
        return f"{instance_url.rstrip('/')}/api/v1/storage/gdrive/oauth/callback"
    return str(request.url_for("google_drive_oauth_callback"))


@router.get("/stats", response_model=List[StorageStats])
def get_storage_stats(db: Annotated[Session, Depends(get_db)]):
    """Get storage statistics for all cold storage locations."""
    locations = db.query(ColdStorageLocation).all()

    unique_volumes = {}
    for location in locations:
        if location.backend_type != ColdStorageBackendType.LOCAL:
            unique_volumes[f"remote_{location.id}"] = location.path
            continue
        path_str = location.path
        try:
            # Get the device ID for the path
            device_id = Path(path_str).stat().st_dev
            if device_id not in unique_volumes:
                unique_volumes[device_id] = path_str
        except FileNotFoundError:
            # Handle cases where the path doesn't exist
            if "not_found" not in unique_volumes:
                unique_volumes["not_found"] = []
            unique_volumes["not_found"].append(path_str)
        except Exception:
            # Handle other potential errors
            logger.exception(f"Error stating path {path_str}")
            if "error" not in unique_volumes:
                unique_volumes["error"] = []
            unique_volumes["error"].append(path_str)

    stats_list = []
    for device_id, path_str in unique_volumes.items():
        if device_id in {"not_found", "error"}:
            for p in path_str:
                stats_list.append(
                    StorageStats(
                        path=p,
                        total_bytes=0,
                        used_bytes=0,
                        free_bytes=0,
                        error="Path not found or error stating path.",
                    )
                )
            continue
        if isinstance(device_id, str) and device_id.startswith("remote_"):
            location_id = int(device_id.removeprefix("remote_"))
            location = next((loc for loc in locations if loc.id == location_id), None)
            if not location:
                continue
            backend = get_backend(location)

            if location.backend_type == ColdStorageBackendType.GDRIVE and hasattr(
                backend, "get_usage_stats"
            ):
                try:
                    usage = backend.get_usage_stats(location)
                    total_bytes = int(usage.get("total_limit_bytes") or 0)
                    used_bytes = int(usage.get("total_used_bytes") or 0)
                    free_bytes = (
                        int(usage["free_bytes"])
                        if usage.get("free_bytes") is not None
                        else max(0, total_bytes - used_bytes)
                    )
                    stats_list.append(
                        StorageStats(
                            path=path_str,
                            total_bytes=total_bytes,
                            used_bytes=used_bytes,
                            free_bytes=free_bytes,
                            app_used_bytes=int(usage.get("app_used_bytes") or 0),
                        )
                    )
                except Exception as exc:
                    logger.exception("Error getting Google Drive stats for %s", location.name)
                    stats_list.append(
                        StorageStats(
                            path=path_str,
                            total_bytes=0,
                            used_bytes=0,
                            free_bytes=0,
                            error=f"Google Drive stats unavailable: {exc}",
                        )
                    )
            else:
                stats_list.append(
                    StorageStats(
                        path=path_str,
                        total_bytes=0,
                        used_bytes=0,
                        free_bytes=0,
                        error="Not available for non-local storage backend.",
                    )
                )
            continue

        try:
            total, used, free = shutil.disk_usage(path_str)
            stats_list.append(
                StorageStats(
                    path=path_str,
                    total_bytes=total,
                    used_bytes=used,
                    free_bytes=free,
                )
            )
        except Exception as e:
            logger.exception(f"Error getting disk usage for {path_str}")
            stats_list.append(
                StorageStats(
                    path=path_str,
                    total_bytes=0,
                    used_bytes=0,
                    free_bytes=0,
                    error=str(e),
                )
            )

    return stats_list


@router.get("/locations/{location_id}/gdrive/files")
def list_google_drive_files(
    location_id: int,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(100, ge=1, le=1000),
    page_token: Optional[str] = Query(None),
):
    """List File Fridge managed files in a Google Drive cold storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    if location.backend_type != ColdStorageBackendType.GDRIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only available for Google Drive backends",
        )

    backend = get_backend(location)
    if not hasattr(backend, "list_managed_files"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Backend does not support file listing",
        )

    try:
        listing = backend.list_managed_files(location, page_size=limit, page_token=page_token)
        return {
            "location_id": location.id,
            "files": listing.get("files", []),
            "next_page_token": listing.get("next_page_token"),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list Google Drive files: {exc}",
        ) from exc


@router.get("/locations/{location_id}/gdrive/stats")
def get_google_drive_stats(location_id: int, db: Annotated[Session, Depends(get_db)]):
    """Get Google Drive quota and File Fridge usage for a storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    if location.backend_type != ColdStorageBackendType.GDRIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only available for Google Drive backends",
        )

    backend = get_backend(location)
    if not hasattr(backend, "get_usage_stats"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Backend does not support usage statistics",
        )

    try:
        usage = backend.get_usage_stats(location)
        # get_app_usage_bytes paginates all managed files — intentionally called
        # only from this dedicated endpoint, not from the general stats endpoint.
        app_used_bytes = (
            backend.get_app_usage_bytes(location)
            if hasattr(backend, "get_app_usage_bytes")
            else None
        )
        return {
            "location_id": location.id,
            "location_name": location.name,
            "drive_total_bytes": usage.get("total_limit_bytes"),
            "drive_used_bytes": usage.get("total_used_bytes"),
            "drive_free_bytes": usage.get("free_bytes"),
            "drive_usage_in_drive_bytes": usage.get("usage_in_drive_bytes"),
            "drive_usage_in_drive_trash_bytes": usage.get("usage_in_drive_trash_bytes"),
            "app_used_bytes": app_used_bytes,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load Google Drive stats: {exc}",
        ) from exc


@router.get("/gdrive/oauth/metadata")
def get_google_drive_oauth_metadata(request: Request, db: Annotated[Session, Depends(get_db)]):
    """Return Google OAuth metadata used by the storage setup UI."""
    return {"callback_url": _get_google_oauth_redirect_uri(request, db)}


# ColdStorageLocation CRUD endpoints


@router.get("/locations", response_model=List[ColdStorageLocationWithStats])
def list_storage_locations(
    db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100
):
    """List all cold storage locations."""
    locations = db.query(ColdStorageLocation).offset(skip).limit(limit).all()
    return [_location_to_schema(loc, path_count=len(loc.paths)) for loc in locations]


@router.post(
    "/locations", response_model=ColdStorageLocationSchema, status_code=status.HTTP_201_CREATED
)
def create_storage_location(
    location: ColdStorageLocationCreate, db: Annotated[Session, Depends(get_db)]
):
    """Create a new cold storage location."""
    backend_config = location.backend_config or {}
    effective_path = location.path
    if location.backend_type == ColdStorageBackendType.S3:
        bucket = (backend_config.get("bucket") or "").strip()
        prefix = (backend_config.get("prefix") or "").strip("/")
        effective_path = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
    elif location.backend_type == ColdStorageBackendType.GDRIVE:
        folder_id = (backend_config.get("folder_id") or "").strip()
        if not folder_id:
            # Store the location name as the folder selector so Drive operations
            # can find/create the right folder rather than uploading to Drive root.
            backend_config["folder_id"] = location.name
        effective_path = f"gdrive://{folder_id or location.name}"

    # Check for duplicate name
    existing_name = (
        db.query(ColdStorageLocation).filter(ColdStorageLocation.name == location.name).first()
    )
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location with name '{location.name}' already exists",
        )

    # Check for duplicate path
    existing_path = (
        db.query(ColdStorageLocation).filter(ColdStorageLocation.path == effective_path).first()
    )
    if existing_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location with path '{effective_path}' already exists",
        )

    location_data = location.model_dump(exclude={"backend_config"})
    location_data["path"] = effective_path
    db_location = ColdStorageLocation(**location_data)
    db_location.set_backend_config(backend_config)

    if location.backend_type == ColdStorageBackendType.LOCAL:
        # Validate local path exists
        path_obj = Path(location.path)
        if not path_obj.exists():
            try:
                path_obj.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create storage location path: {e!s}",
                ) from e

        if not path_obj.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {location.path}",
            )

    update_local_drive_identity_fields(db_location)
    backend = get_backend(db_location)
    is_valid, validation_error = backend.validate_location(db_location)
    if not is_valid and not _can_tolerate_offline(db_location, validation_error):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation_error)

    db.add(db_location)
    db.commit()
    db.refresh(db_location)

    return _location_to_schema(db_location)


@router.get("/locations/{location_id}", response_model=ColdStorageLocationSchema)
def get_storage_location(location_id: int, db: Annotated[Session, Depends(get_db)]):
    """Get a specific cold storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    return _location_to_schema(location)


@router.put("/locations/{location_id}", response_model=ColdStorageLocationSchema)
def update_storage_location(
    location_id: int,
    location_update: ColdStorageLocationUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    """Update a cold storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )

    update_data = location_update.model_dump(exclude_unset=True)

    # Track if we need to trigger a background job
    trigger_encryption_job = False
    trigger_decryption_job = False

    # Handle Encryption Toggle
    # Track previous state for rollback if scheduling fails
    previous_is_encrypted = location.is_encrypted
    previous_encryption_status = location.encryption_status

    if "is_encrypted" in update_data:
        new_encrypted_state = update_data["is_encrypted"]
        if new_encrypted_state != location.is_encrypted:
            # State changed
            if new_encrypted_state:
                # Enabling encryption
                location.encryption_status = EncryptionStatus.PENDING
                trigger_encryption_job = True
            else:
                # Disabling encryption
                location.encryption_status = EncryptionStatus.DECRYPTING
                trigger_decryption_job = True

    # Check for duplicate name if name is being updated
    if "name" in update_data:
        existing = (
            db.query(ColdStorageLocation)
            .filter(
                ColdStorageLocation.name == update_data["name"],
                ColdStorageLocation.id != location_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Storage location with name '{update_data['name']}' already exists",
            )

    backend_config = update_data.pop("backend_config", None)

    # Update fields
    for field, value in update_data.items():
        setattr(location, field, value)
    if backend_config is not None:
        existing_config = location.get_backend_config()
        merged_config = dict(existing_config)
        protected_gdrive_auth_keys = {
            "refresh_token",
            "access_token",
            "access_token_expires_at",
        }
        for key, value in backend_config.items():
            if value == "***REDACTED***":
                continue
            # Keep OAuth-issued Google auth tokens stable unless refreshed by the
            # dedicated OAuth callback flow. Some clients may send null/empty
            # values for hidden fields during generic edits.
            if (
                location.backend_type == ColdStorageBackendType.GDRIVE
                and key in protected_gdrive_auth_keys
                and (value is None or value == "")
            ):
                continue
            if value is None:
                merged_config.pop(key, None)
            else:
                merged_config[key] = value
        location.set_backend_config(merged_config)

    if location.backend_type == ColdStorageBackendType.S3:
        cfg = location.get_backend_config()
        bucket = (cfg.get("bucket") or "").strip()
        prefix = (cfg.get("prefix") or "").strip("/")
        location.path = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
    elif location.backend_type == ColdStorageBackendType.GDRIVE:
        cfg = location.get_backend_config()
        folder_id = (cfg.get("folder_id") or "").strip()
        if not folder_id:
            cfg["folder_id"] = location.name
            location.set_backend_config(cfg)
        location.path = f"gdrive://{folder_id or location.name}"
    else:
        target_path = update_data.get("path", location.path)
        path_obj = Path(target_path)
        if not path_obj.exists():
            try:
                path_obj.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create storage location path: {e!s}",
                ) from e
        if not path_obj.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {target_path}",
            )
        location.path = target_path

    existing_path = (
        db.query(ColdStorageLocation)
        .filter(ColdStorageLocation.path == location.path, ColdStorageLocation.id != location_id)
        .first()
    )
    if existing_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location with path '{location.path}' already exists",
        )

    update_local_drive_identity_fields(location)
    backend = get_backend(location)
    is_valid, validation_error = backend.validate_location(location)
    if not is_valid and not _can_tolerate_offline(location, validation_error):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation_error)

    db.commit()
    db.refresh(location)

    # Trigger background jobs after commit
    if trigger_encryption_job:
        try:
            scheduler_service.trigger_encryption_job(location.id)
            logger.info(
                "Encryption enabled for location %s. Queuing background encryption job.",
                location.name,
            )
        except Exception as e:
            logger.exception("Failed to schedule encryption job for location %s", location.name)
            # Rollback encryption state in database
            try:
                location.is_encrypted = previous_is_encrypted
                location.encryption_status = previous_encryption_status
                db.commit()
            except Exception:
                logger.exception(
                    "Failed to rollback encryption state for location %s", location.name
                )
                db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to schedule encryption job: {e!s}",
            ) from e
    elif trigger_decryption_job:
        try:
            scheduler_service.trigger_decryption_job(location.id)
            logger.info(
                "Encryption disabled for location %s. Queuing background decryption job.",
                location.name,
            )
        except Exception as e:
            logger.exception("Failed to schedule decryption job for location %s", location.name)
            # Rollback encryption state in database
            try:
                location.is_encrypted = previous_is_encrypted
                location.encryption_status = previous_encryption_status
                db.commit()
            except Exception:
                logger.exception(
                    "Failed to rollback encryption state for location %s", location.name
                )
                db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to schedule decryption job: {e!s}",
            ) from e

    return _location_to_schema(location)


@router.post("/locations/{location_id}/pause", response_model=ColdStorageLocationSchema)
def pause_storage_location(location_id: int, db: Annotated[Session, Depends(get_db)]):
    """Pause a cold storage location so no new files are routed to it."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    location.paused = True
    db.commit()
    db.refresh(location)
    return _location_to_schema(location)


@router.post("/locations/{location_id}/unpause", response_model=ColdStorageLocationSchema)
def unpause_storage_location(location_id: int, db: Annotated[Session, Depends(get_db)]):
    """Unpause a cold storage location to resume routing files to it."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    location.paused = False
    db.commit()
    db.refresh(location)
    return _location_to_schema(location)


@router.post("/locations/{location_id}/recall", status_code=status.HTTP_202_ACCEPTED)
def recall_storage_location(location_id: int, db: Annotated[Session, Depends(get_db)]):
    """Trigger a background job to thaw all files from a cold storage location back to hot storage."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )

    cold_file_count = (
        db.query(FileInventory)
        .filter(
            FileInventory.cold_storage_location_id == location_id,
            FileInventory.storage_type == "cold",
        )
        .count()
    )

    try:
        scheduler_service.trigger_recall_job(location_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to schedule recall job: {e!s}",
        ) from e

    return {
        "message": f"Recall job started for location '{location.name}'",
        "location_id": location_id,
        "files_queued": cold_file_count,
    }


@router.post("/locations/{location_id}/gdrive/oauth/start")
def start_google_drive_oauth(
    location_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    """Start Google OAuth flow for a Google Drive storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    if location.backend_type != ColdStorageBackendType.GDRIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth is only available for Google Drive storage locations",
        )

    config = location.get_backend_config()
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set Google client ID and client secret before connecting",
        )

    redirect_uri = _get_google_oauth_redirect_uri(request, db)
    state = _make_gdrive_state(location_id)
    auth_query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # drive.file scope limits access to files created by this app only,
            # which is the least-privilege option for enterprise deployments.
            "scope": "https://www.googleapis.com/auth/drive.file",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{auth_query}"
    return {"auth_url": auth_url}


@public_router.get("/gdrive/oauth/callback", name="google_drive_oauth_callback")
def google_drive_oauth_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle Google OAuth callback and persist refresh token."""
    if error:
        return RedirectResponse(
            url=f"/storage-locations?gdrive_oauth=error&reason={url_quote(error)}"
        )
    if not code or not state:
        return RedirectResponse(url="/storage-locations?gdrive_oauth=error&reason=missing_code")

    try:
        location_id = _parse_gdrive_state(state)
        location = (
            db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
        )
        if not location or location.backend_type != ColdStorageBackendType.GDRIVE:
            return RedirectResponse(
                url="/storage-locations?gdrive_oauth=error&reason=invalid_location"
            )

        cfg = location.get_backend_config()
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        if not client_id or not client_secret:
            return RedirectResponse(
                url=f"/storage-locations/{location_id}/edit?gdrive_oauth=error&reason=missing_client_credentials"
            )

        redirect_uri = _get_google_oauth_redirect_uri(request, db)
        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        refresh_token = token_data.get("refresh_token")
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in")
        if not refresh_token:
            # Google may omit refresh token if prior consent exists and prompt was bypassed.
            refresh_token = cfg.get("refresh_token")
        if not refresh_token:
            return RedirectResponse(
                url=f"/storage-locations/{location_id}/edit?gdrive_oauth=error&reason=missing_refresh_token"
            )

        cfg["refresh_token"] = refresh_token
        if access_token:
            cfg["access_token"] = access_token
        if expires_in:
            cfg["access_token_expires_at"] = int(time.time()) + int(expires_in)
        location.set_backend_config(cfg)
        db.commit()
        return RedirectResponse(url=f"/storage-locations/{location_id}/edit?gdrive_oauth=success")
    except httpx.ConnectError:
        logger.exception("Google OAuth callback failed due to network connectivity")
        return RedirectResponse(
            url="/storage-locations?gdrive_oauth=error&reason=network_unreachable"
        )
    except Exception:
        logger.exception("Google OAuth callback failed")
        return RedirectResponse(url="/storage-locations?gdrive_oauth=error&reason=callback_failed")


@router.delete("/locations/{location_id}", status_code=status.HTTP_200_OK)
def delete_storage_location(
    location_id: int,
    db: Annotated[Session, Depends(get_db)],
    force: bool = Query(False, description="Force delete the location even if it's not empty"),
):
    """
    Delete a cold storage location.

    - If `force` is False, this will fail if the location is still associated with any monitored paths.
    - If `force` is True, it will remove all associated file records and attempt to delete the files from storage.
      This is useful for corrupted or lost drives.
    """
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )

    # Standard delete: Check if location is still in use by monitored paths
    if not force and location.paths:
        path_names = [p.name for p in location.paths]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete storage location '{location.name}' because it is still associated with "
            f"{len(location.paths)} monitored path(s): {', '.join(path_names)}",
        )

    # Force delete: Clean up all associated data
    if force:
        logger.info(f"Force deleting storage location '{location.name}' (ID: {location_id})")

        # 1. Find all files in this storage location
        # We need to check both FileInventory and FileRecord for paths
        # Ensure path ends with slash to prevent partial matches (e.g. /data/cold matching /data/cold_backup)
        location_path = location.path if location.path.endswith("/") else f"{location.path}/"
        escaped_path = escape_like_string(location_path)

        inventory_files = (
            db.query(FileInventory)
            .filter(
                FileInventory.file_path.like(f"{escaped_path}%", escape="\\"),
                FileInventory.cold_storage_location_id == location_id,
            )
            .all()
        )
        file_records = (
            db.query(FileRecord)
            .filter(
                FileRecord.cold_storage_path.like(f"{escaped_path}%", escape="\\"),
                FileRecord.cold_storage_location_id == location_id,
            )
            .all()
        )

        # 2. Delete file records from the database
        for record in file_records:
            db.delete(record)

        for inv_file in inventory_files:
            db.delete(inv_file)

        # 3. Attempt to delete the actual files and directory from the filesystem
        try:
            if (
                location.backend_type == ColdStorageBackendType.LOCAL
                and Path(location.path).exists()
            ):
                logger.info(f"Deleting files and directory: {location.path}")
                shutil.rmtree(location.path)
        except FileNotFoundError:
            logger.warning(f"Path not found, proceeding with DB deletion: {location.path}")
        except Exception as e:
            logger.exception(
                f"Error deleting storage directory '{location.path}'. "
                f"Manual cleanup may be required.",
                exc_info=e,
            )
            # We don't re-raise, to allow DB cleanup to proceed

        # 4. Disassociate monitored paths
        location.paths.clear()

        db.commit()  # Commit record deletions and path disassociation

    # Delete the location itself
    db.delete(location)
    db.commit()

    return {"message": f"Storage location '{location.name}' deleted successfully"}
