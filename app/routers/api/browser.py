# ruff: noqa: B008
"""API routes for file system browsing."""

import logging
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ColdStorageLocation, FileInventory, MonitoredPath
from app.schemas import BrowserItem, BrowserResponse
from app.security import get_current_user

router = APIRouter(prefix="/api/v1/browser", tags=["browser"])
logger = logging.getLogger(__name__)


def _sanitize_log_input(input_str: str) -> str:
    """Sanitize input string for logging to prevent log injection."""
    return input_str.replace("\n", "\\n").replace("\r", "\\r")


@router.get("/list", response_model=BrowserResponse)
def list_directory(  # noqa: PLR0912, PLR0915
    path: str = Query("/", description="Directory path to browse"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Browse a directory and return its contents with inventory status.

    This endpoint is unrestricted (admins can browse anywhere) and includes
    inventory status for files that are tracked in the database.

    Args:
        path: Directory path to browse (defaults to root)
        db: Database session
        current_user: Authenticated user (admin access required)

    Returns:
        BrowserResponse with directory contents and statistics

    Raises:
        HTTPException: 400 if path is invalid, 404 if path doesn't exist
    """
    try:
        # Resolve the path to handle any '..' or symlinks
        try:
            resolved_path = Path(path).resolve()
        except (OSError, ValueError) as e:
            # Don't expose internal path details on error
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid directory path",
            ) from e

        # SECURITY: Validate that the requested path is within a configured monitored path or storage location
        # Admins are exempt from this check
        is_admin = "admin" in current_user.roles

        if not is_admin:
            monitored_paths = db.query(MonitoredPath.source_path).all()
            storage_paths = db.query(ColdStorageLocation.path).all()

            allowed_bases = [Path(p[0]).resolve() for p in monitored_paths] + [
                Path(p[0]).resolve() for p in storage_paths
            ]

            is_allowed = False
            for base in allowed_bases:
                try:
                    # Check if resolved_path is base or a subdirectory of base
                    # resolved_path is already resolved above
                    if resolved_path == base or base in resolved_path.parents:
                        is_allowed = True
                        break
                except ValueError:
                    continue

            if not is_allowed:
                user_log = _sanitize_log_input(current_user.username)
                path_log = _sanitize_log_input(path)
                logger.warning(f"Unauthorized directory browse attempt by {user_log}: {path_log}")
                raise HTTPException(  # noqa: TRY301
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Directory is not within a configured monitored path or storage location",
                )

        # Verify path exists and is a directory
        if not resolved_path.exists():
            raise HTTPException(  # noqa: TRY301
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Directory does not exist",
            )

        if not resolved_path.is_dir():
            raise HTTPException(  # noqa: TRY301
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is not a directory",
            )

        # Get inventory status for all files in this directory
        # Build a map of file_path -> inventory_status
        inventory_map: Dict[str, str] = {}
        try:
            # Query all files in the current directory from inventory
            # Use startswith for safer prefix matching than LIKE
            prefix = str(resolved_path)
            if not prefix.endswith("/"):
                prefix += "/"

            inventory_entries = (
                db.query(FileInventory.file_path, FileInventory.storage_type)
                .filter(FileInventory.file_path.startswith(prefix))
                .all()
            )

            for file_path, storage_type in inventory_entries:
                # Only include files directly in this directory (not subdirectories)
                if Path(file_path).parent == resolved_path:
                    inventory_map[file_path] = (
                        storage_type.value if hasattr(storage_type, "value") else str(storage_type)
                    )
        except Exception as e:
            logger.warning(f"Failed to fetch inventory status: {e}")
            # Continue without inventory status on error

        # List directory contents
        items = []
        total_files = 0
        total_dirs = 0

        for item in sorted(resolved_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                stat_info = item.stat()
                is_dir = item.is_dir()

                # Get inventory status for files
                inventory_status = None
                if not is_dir:
                    inventory_status = inventory_map.get(str(item))

                browser_item = BrowserItem(
                    name=item.name,
                    path=str(item),
                    is_dir=is_dir,
                    size=stat_info.st_size if not is_dir else 0,
                    modified=stat_info.st_mtime,
                    inventory_status=inventory_status,
                )

                items.append(browser_item)

                if is_dir:
                    total_dirs += 1
                else:
                    total_files += 1

            except (OSError, PermissionError) as e:
                # Skip items we can't access
                logger.debug(f"Skipping inaccessible item {item}: {e}")
                continue

        return BrowserResponse(
            current_path=str(resolved_path),
            total_items=len(items),
            total_files=total_files,
            total_dirs=total_dirs,
            items=items,
        )

    except HTTPException:
        raise
    except Exception as e:
        # Don't leak internal path in exception details
        logger.exception(f"Error browsing directory {_sanitize_log_input(path)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error browsing directory",
        ) from e
