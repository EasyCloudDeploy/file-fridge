import logging
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, MonitoredPath, User

logger = logging.getLogger(__name__)


def check_path_permission(db: Session, current_user: User, resolved_path: Path) -> None:
    """
    Check if the current user has permission to access the resolved path.

    Admins have unrestricted access. Other users can only access paths that
    are within monitored paths or cold storage locations.

    Args:
        db: Database session
        current_user: Authenticated user
        resolved_path: The absolute path to access

    Raises:
        HTTPException(403): If permission is denied.
    """
    if "admin" in current_user.roles:
        return

    allowed_paths = []

    # Get monitored paths
    monitored_paths = db.query(MonitoredPath.source_path).all()
    for p in monitored_paths:
        try:
            allowed_paths.append(Path(p[0]).resolve())
        except (OSError, ValueError):
            continue

    # Get cold storage locations
    cold_locations = db.query(ColdStorageLocation.path).all()
    for p in cold_locations:
        try:
            allowed_paths.append(Path(p[0]).resolve())
        except (OSError, ValueError):
            continue

    is_allowed = False
    for allowed_path in allowed_paths:
        try:
            resolved_path.relative_to(allowed_path)
            is_allowed = True
            break
        except ValueError:
            continue

    if not is_allowed:
        logger.warning(
            f"Permission denied for user {current_user.username}: "
            f"Access attempted to {resolved_path} which is not in allowed paths."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )


# Alias for backward compatibility
check_browser_permissions = check_path_permission
