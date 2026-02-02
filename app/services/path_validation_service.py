import logging
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, MonitoredPath, User

logger = logging.getLogger(__name__)


def validate_path_access(user: User, path: Path, db: Session) -> None:
    """
    Validate that the user has permission to access the given path.

    Admins have full access.
    Other users are restricted to configured MonitoredPaths and ColdStorageLocations.

    Args:
        user: The authenticated user
        path: The path to validate (must be resolved/absolute)
        db: Database session

    Raises:
        HTTPException(403): If access is denied
    """
    # Admins can access everything
    if "admin" in user.roles:
        return

    # Check against allowed bases
    monitored_paths = db.query(MonitoredPath.source_path).all()
    storage_paths = db.query(ColdStorageLocation.path).all()

    allowed_bases = [Path(p[0]).resolve() for p in monitored_paths] + [
        Path(p[0]).resolve() for p in storage_paths
    ]

    is_allowed = False
    for base in allowed_bases:
        try:
            # Check if path is base or a subdirectory of base
            path.relative_to(base)
            is_allowed = True
            break
        except ValueError:
            continue

    if not is_allowed:
        # Sanitize log inputs to prevent log injection
        safe_username = user.username.replace("\n", "").replace("\r", "")
        safe_path = str(path).replace("\n", "").replace("\r", "")

        # Use structured logging args to avoid F-string log injection hotspots
        logger.warning(
            "Unauthorized directory access attempt by user %s: %s", safe_username, safe_path
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Directory is not within a configured monitored path or storage location",
        )
