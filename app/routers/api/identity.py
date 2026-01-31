import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RemoteConnection, User
from app.schemas import (
    IdentityExportResponse,
    IdentityImportRequest,
    IdentityPublicExportResponse,
    PrivateExportRequest,
)
from app.security import PermissionChecker, verify_password
from app.services.identity_service import identity_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/identity", tags=["Identity"])


@router.get("/public-export", response_model=IdentityPublicExportResponse)
def export_public_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionChecker("admin")),
):
    """
    Export the instance's public keys in PEM format.
    """
    keys = identity_service.export_keys_pem(db)
    return {
        "signing_public_key": keys["signing_public_key"],
        "kx_public_key": keys["kx_public_key"],
    }


@router.post("/private-export", response_model=IdentityExportResponse)
def export_private_keys(
    request: PrivateExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionChecker("admin")),
):
    """
    Export the instance's private and public keys in PEM format.
    Requires the administrator's password for verification.
    """
    if not verify_password(request.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    return identity_service.export_keys_pem(db)


@router.post("/import", status_code=status.HTTP_200_OK)
def import_identity(
    request: IdentityImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionChecker("admin")),
):
    """
    Import a new identity (private keys).
    WARNING: This will replace the current instance identity and invalidate existing remote connections.
    """
    if not verify_password(request.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    # Check for existing remote connections
    existing_connections_count = db.query(RemoteConnection).count()
    if existing_connections_count > 0:
        if not request.confirm_replace:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This action will delete {existing_connections_count} existing remote connections. "
                "Please confirm by setting confirm_replace=True.",
            )

    try:
        identity_service.import_keys_pem(db, request.signing_private_key, request.kx_private_key)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Delete existing connections
    if existing_connections_count > 0:
        logger.warning(
            f"Deleting {existing_connections_count} remote connections due to identity import by {current_user.username}"
        )
        db.query(RemoteConnection).delete()
        db.commit()

    return {"message": "Identity imported successfully."}
