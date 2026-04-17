"""Helpers for detecting and tracking local removable drive identity."""

from __future__ import annotations

import platform
import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app.models import ColdStorageBackendType, ColdStorageLocation


def _diskutil_info(path: Path) -> Dict:
    """Return diskutil plist info for a path on macOS, or empty dict if unavailable."""
    if platform.system() != "Darwin":
        return {}
    try:
        proc = subprocess.run(
            ["diskutil", "info", "-plist", "--", str(path)],
            check=True,
            capture_output=True,
            text=False,
        )
        return plistlib.loads(proc.stdout)
    except Exception:
        return {}


def detect_local_drive_identity(path: Path) -> Dict[str, Optional[str]]:
    """
    Detect local drive identity from path metadata.

    Returns best-effort fields:
    - identifier: stable-ish drive identifier
    - label: user-friendly volume label
    - mount_path: mount point for the volume
    - is_removable: whether the volume appears removable
    - is_connected: whether path currently resolves
    """
    info: Dict[str, Optional[str]] = {
        "identifier": None,
        "label": None,
        "mount_path": None,
        "is_removable": False,
        "is_connected": path.exists(),
    }

    if not path.exists():
        return info

    # Best effort POSIX fallback.
    try:
        stat_info = path.stat()
        info["identifier"] = f"dev:{stat_info.st_dev}"
    except Exception:
        pass

    # macOS diskutil gives more stable identity/label details.
    diskutil = _diskutil_info(path)
    if diskutil:
        identifier = (
            diskutil.get("VolumeUUID")
            or diskutil.get("DiskUUID")
            or diskutil.get("DeviceIdentifier")
            or info["identifier"]
        )
        info["identifier"] = str(identifier) if identifier else info["identifier"]
        label = diskutil.get("VolumeName") or diskutil.get("MediaName")
        info["label"] = str(label) if label else info["label"]
        mount_point = diskutil.get("MountPoint")
        info["mount_path"] = str(mount_point) if mount_point else str(path)
        removable_media = diskutil.get("RemovableMedia")
        if isinstance(removable_media, bool):
            info["is_removable"] = removable_media
    else:
        info["mount_path"] = str(path)

    return info


def update_local_drive_identity_fields(
    location: ColdStorageLocation, now: Optional[datetime] = None
) -> None:
    """Refresh local-drive identity/status fields in-place for a location."""
    if location.backend_type != ColdStorageBackendType.LOCAL:
        location.local_drive_identifier = None
        location.local_drive_label = None
        location.local_drive_mount_path = None
        location.local_drive_is_removable = False
        location.local_drive_is_connected = True
        location.local_drive_last_seen_at = None
        return

    path = Path(location.path)
    detected = detect_local_drive_identity(path)

    location.local_drive_is_connected = bool(detected.get("is_connected"))

    if location.local_drive_is_connected:
        if detected.get("identifier"):
            location.local_drive_identifier = str(detected["identifier"])
        if detected.get("label"):
            location.local_drive_label = str(detected["label"])
        if detected.get("mount_path"):
            location.local_drive_mount_path = str(detected["mount_path"])
        location.local_drive_is_removable = bool(detected.get("is_removable"))
        location.local_drive_last_seen_at = now or datetime.now(tz=timezone.utc)
