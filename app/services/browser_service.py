from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import ColdStorageLocation, MonitoredPath, User


def check_browser_permissions(db: Session, current_user: User, resolved_path: Path) -> None:
    """
    Check if the current user has permission to browse the resolved path.

    Admins have unrestricted access. Other users can only browse paths that
    are within monitored paths or cold storage locations.

    Args:
        db: Database session
        current_user: Authenticated user
        resolved_path: The absolute path to browse

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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied: You can only browse monitored paths and cold storage locations.",
        )
