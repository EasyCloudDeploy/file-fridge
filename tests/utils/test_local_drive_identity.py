"""Tests for local drive identity helpers."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import ColdStorageBackendType, ColdStorageLocation
from app.utils import local_drive_identity as ldi


@pytest.mark.unit
class TestLocalDriveIdentity:
    def test_diskutil_info_non_darwin(self, monkeypatch):
        monkeypatch.setattr(ldi.platform, "system", lambda: "Linux")
        assert ldi._diskutil_info(Path("/tmp")) == {}

    def test_diskutil_info_darwin_success(self, monkeypatch):
        monkeypatch.setattr(ldi.platform, "system", lambda: "Darwin")
        plist = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>VolumeUUID</key><string>UUID-1</string>
<key>VolumeName</key><string>MyVol</string>
<key>MountPoint</key><string>/Volumes/MyVol</string>
<key>RemovableMedia</key><true/>
</dict></plist>"""

        proc = MagicMock(returncode=0, stdout=plist)
        monkeypatch.setattr(ldi.subprocess, "run", MagicMock(return_value=proc))
        info = ldi._diskutil_info(Path("/Volumes/MyVol"))
        assert info["VolumeUUID"] == "UUID-1"

    def test_diskutil_info_darwin_failure(self, monkeypatch):
        monkeypatch.setattr(ldi.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            ldi.subprocess, "run", MagicMock(side_effect=OSError("diskutil missing"))
        )
        assert ldi._diskutil_info(Path("/tmp")) == {}

    def test_detect_missing_path(self, tmp_path):
        missing = tmp_path / "gone"
        detected = ldi.detect_local_drive_identity(missing)
        assert detected["is_connected"] is False
        assert detected["identifier"] is None

    def test_detect_local_path_posix(self, tmp_path):
        vol = tmp_path / "vol"
        vol.mkdir()
        detected = ldi.detect_local_drive_identity(vol)
        assert detected["is_connected"] is True
        assert detected["identifier"] is not None
        assert detected["mount_path"] == str(vol)

    def test_detect_diskutil_identifier_fallbacks(self, tmp_path, monkeypatch):
        vol = tmp_path / "id_fallback"
        vol.mkdir()
        monkeypatch.setattr(ldi.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            ldi,
            "_diskutil_info",
            lambda _p: {
                "DeviceIdentifier": "disk3s1",
                "MediaName": "Media",
                "RemovableMedia": False,
            },
        )
        detected = ldi.detect_local_drive_identity(vol)
        assert detected["identifier"] == "disk3s1"
        assert detected["label"] == "Media"
        assert detected["is_removable"] is False

    def test_detect_uses_diskutil_on_darwin(self, tmp_path, monkeypatch):
        vol = tmp_path / "darwin_vol"
        vol.mkdir()
        monkeypatch.setattr(ldi.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            ldi,
            "_diskutil_info",
            lambda _p: {
                "VolumeUUID": "vol-uuid",
                "VolumeName": "Backup",
                "MountPoint": "/Volumes/Backup",
                "RemovableMedia": True,
            },
        )
        detected = ldi.detect_local_drive_identity(vol)
        assert detected["identifier"] == "vol-uuid"
        assert detected["label"] == "Backup"
        assert detected["is_removable"] is True

    def test_update_non_local_clears_fields(self):
        loc = ColdStorageLocation(
            name="S3",
            path="s3://b",
            backend_type=ColdStorageBackendType.S3,
            local_drive_identifier="old",
            local_drive_label="old",
        )
        ldi.update_local_drive_identity_fields(loc)
        assert loc.local_drive_identifier is None
        assert loc.local_drive_is_connected is True

    def test_update_local_connected(self, tmp_path):
        cold = tmp_path / "cold_drive"
        cold.mkdir()
        loc = ColdStorageLocation(
            name="USB",
            path=str(cold),
            backend_type=ColdStorageBackendType.LOCAL,
        )
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with patch.object(
            ldi,
            "detect_local_drive_identity",
            return_value={
                "identifier": "dev:99",
                "label": "USB Stick",
                "mount_path": str(cold),
                "is_removable": True,
                "is_connected": True,
            },
        ):
            ldi.update_local_drive_identity_fields(loc, now=now)
        assert loc.local_drive_identifier == "dev:99"
        assert loc.local_drive_last_seen_at == now
