"""Utility functions for detecting network mounts and filesystem characteristics."""
import logging
import os
import platform
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# TODO: We should use the statfs API to check if a path is on a network mount
def is_network_mount(path: str) -> bool:
    """
    Detect if a path is on a network mount.
    
    On macOS, checks if the path is under /Volumes (which includes network mounts)
    and excludes the local boot disk.
    
    Args:
        path: The path to check
        
    Returns:
        True if the path is on a network mount, False otherwise
    """
    try:
        path_obj = Path(path).resolve()

        if platform.system() == "Darwin":  # macOS

            # Test if the path is a mount

            # Check if it's under /Volumes (common mount point for network shares)
            if str(path_obj).startswith("/Volumes/"):
                # Get the volume name (first component after /Volumes/)
                volume_parts = path_obj.parts
                if len(volume_parts) >= 2:
                    volume_name = volume_parts[1]  # e.g., "data" from "/Volumes/data/..."

                    # Common local disk names (exclude these)
                    local_disk_names = ["Macintosh HD", "Macintosh HD - Data", "System"]

                    # If it's not a known local disk name, it's likely a network mount
                    if volume_name not in local_disk_names:
                        # Additional check: compare filesystem IDs with root
                        # Network mounts typically have different f_fsid than the root filesystem
                        try:
                            stat_result = os.statvfs(str(path_obj))
                            root_stat = os.statvfs("/")
                            # If filesystem IDs differ, it's a separate mount (likely network)
                            if stat_result.f_fsid != root_stat.f_fsid:
                                return True
                        except Exception:
                            # If statvfs fails, assume it's a network mount if under /Volumes
                            # and not a known local disk
                            return True

                # Fallback: if under /Volumes and we can't determine, assume network mount
                return True

            # Also check for other network mount patterns
            # SMB/CIFS mounts might be elsewhere, but /Volumes is most common on macOS

        # For other platforms, we could check similar patterns
        # For now, return False for non-macOS systems
        return False

    except Exception as e:
        logger.warning(f"Error checking if path is network mount: {e}")
        return False


def check_atime_availability(cold_storage_path: str) -> tuple[bool, Optional[str]]:
    """
    Check if atime (access time) is reliable for the given cold storage path.
    
    On macOS, atime is unreliable on network mounts because:
    - macOS system services (Spotlight, Finder) update symlink atime
    - Network protocols (SMB/NFS) may not preserve atime correctly
    - Clock drift and precision differences cause oscillation
    
    Args:
        cold_storage_path: The cold storage path to check
        
    Returns:
        (is_available: bool, error_message: Optional[str])
        If atime is unavailable, error_message explains why
    """
    if platform.system() != "Darwin":
        # On non-macOS systems, assume atime is available
        return True, None

    if is_network_mount(cold_storage_path):
        # Check if atime is allowed via settings
        if settings.allow_atime_over_network_mounts:
            return True, None

        # Otherwise, atime is unavailable
        return False, (
            "Access time (atime) functionality is unavailable when cold storage "
            "is on a network mount on macOS. Network mounts have unreliable atime "
            "due to macOS system services (Spotlight, Finder) updating symlink access "
            "times and protocol differences (SMB/NFS). Please use mtime or ctime criteria "
            "instead, or use a local cold storage path."
        )

    return True, None

