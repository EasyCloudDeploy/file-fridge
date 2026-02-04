# ruff: noqa: B008, PLR0912, PLR0915, TRY301, PLC0415, PTH110
"""API routes for file system browsing."""

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ColdStorageLocation, FileInventory, MonitoredPath
from app.schemas import BrowserItem, BrowserResponse
from app.security import get_current_user

router = APIRouter(prefix="/api/v1/browser", tags=["browser"])
logger = logging.getLogger(__name__)


def get_system_roots() -> List[str]:
    """Get the system root paths safely as strings."""
    roots = []
    if sys.platform == "win32":
        # On Windows, list available drives
        import string
        from ctypes import windll

        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                roots.append(f"{letter}:\\")
            bitmask >>= 1
    else:
        # On Linux/Unix, root is just /
        roots.append("/")
    return roots


def is_safe_path(base_path: str, target_path: str) -> bool:
    """
    Check if target_path is safely inside base_path using os.path.commonpath.

    This prevents path traversal attacks by ensuring the resolved target path
    starts with the resolved base path.
    """
    # Use realpath to resolve symlinks and absolute paths
    base_real = os.path.realpath(base_path)
    target_real = os.path.realpath(target_path)

    # Windows drive letter case sensitivity handling
    if sys.platform == "win32":
        base_real = base_real.lower()
        target_real = target_real.lower()

    try:
        # commonpath raises ValueError if paths are on different drives
        return os.path.commonpath([base_real, target_real]) == base_real
    except ValueError:
        return False


@router.get("/list", response_model=BrowserResponse)
def list_directory(
    path: str = Query("/", description="Directory path to browse"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Browse a directory and return its contents with inventory status.

    This endpoint restricts access for non-admin users to only paths defined
    in MonitoredPath or ColdStorageLocation. Admins can browse anywhere.

    Args:
        path: Directory path to browse (defaults to root)
        db: Database session
        current_user: Authenticated user (admin access required)

    Returns:
        BrowserResponse with directory contents and statistics

    Raises:
        HTTPException: 400 if path is invalid, 404 if path doesn't exist, 403 if denied
    """
    try:
        # Sanitize input: Check for null bytes which can be used for bypasses
        if "\0" in path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid characters in path",
            )

        # Validate that the path exists before doing anything else
        if not os.path.exists(path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Directory does not exist",
            )

        # Gather all allowed paths based on role
        allowed_paths: List[str] = []

        if "admin" in current_user.roles:
            # Admins are allowed everything.
            allowed_paths.extend(get_system_roots())
        else:
            # Non-admins: Add monitored paths
            monitored_paths = db.query(MonitoredPath.source_path).all()
            for mp in monitored_paths:
                if mp.source_path:
                    allowed_paths.append(mp.source_path)

            # Non-admins: Add cold storage paths
            cold_paths = db.query(ColdStorageLocation.path).all()
            for cp in cold_paths:
                if cp.path:
                    allowed_paths.append(cp.path)

        # Perform the access check
        is_allowed = False
        for allowed_path in allowed_paths:
            if is_safe_path(allowed_path, path):
                is_allowed = True
                break

        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You do not have permission to browse this directory",
            )

        # Now it is safe to proceed with Path objects
        resolved_path = Path(path).resolve()

        if not resolved_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is not a directory",
            )

        # Get inventory status for all files in this directory
        # Build a map of file_path -> inventory_status
        inventory_map: Dict[str, str] = {}
        try:
            # Query all files in the current directory from inventory
            # Use startswith for security (prevent wildcard injection) and correctness
            inventory_entries = (
                db.query(FileInventory.file_path, FileInventory.storage_type)
                .filter(FileInventory.file_path.startswith(f"{resolved_path}/"))
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
        logger.exception(f"Error browsing directory {path}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error browsing directory: {e!s}",
        ) from e
