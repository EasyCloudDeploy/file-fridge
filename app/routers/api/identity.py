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
    if existing_connections_count > 0 and not request.confirm_replace:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This action will delete {existing_connections_count} existing remote connections. "
            "Please confirm by setting confirm_replace=True.",
        )

    try:
        # Delete existing connections first
        if existing_connections_count > 0:
            logger.warning(
                f"Deleting {existing_connections_count} remote connections due to identity import by {current_user.username}"
            )
            db.query(RemoteConnection).delete()
            # Do not commit yet; wait for key import to succeed

        # Import keys (this handles its own commit in current implementation, but ideally shouldn't if we want shared transaction)
        # identity_service.import_keys_pem calls db.commit().
        # To make this fully atomic, we should modify import_keys_pem to NOT commit, or accept that deletion commits first if we moved it inside.
        # But given constraints, deleting first effectively clears the state for the new identity.
        # If import fails, we roll back the deletion.

        identity_service.import_keys_pem(db, request.signing_private_key, request.kx_private_key)

    except ValueError as e:
        db.rollback() # Rollback deletion if import fails
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error during identity import")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during identity import",
        ) from e

    return {"message": "Identity imported successfully."}
