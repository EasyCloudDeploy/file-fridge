import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
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
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(PermissionChecker("admin"))],
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
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(PermissionChecker("admin"))],
):
    """
    Export the instance's private and public keys in PEM format.
    """
    if not verify_password(request.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    keys = identity_service.export_keys_pem(db)
    return {
        "signing_private_key": keys["signing_private_key"],
        "signing_public_key": keys["signing_public_key"],
        "kx_private_key": keys["kx_private_key"],
        "kx_public_key": keys["kx_public_key"],
    }


@router.post("/import", status_code=status.HTTP_200_OK)
def import_identity(
    request: IdentityImportRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(PermissionChecker("admin"))],
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

    try:
        identity_service.import_keys_pem(db, request.signing_private_key, request.kx_private_key)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Unexpected error during identity import")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during identity import",
        ) from e

    return {"message": "Identity imported successfully."}
