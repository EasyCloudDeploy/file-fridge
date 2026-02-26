import pytest
from pathlib import Path

from app.models import User, MonitoredPath, ColdStorageLocation
from app.services.browser_service import check_path_permission
from fastapi import HTTPException


@pytest.mark.unit
class TestBrowserService:
    def test_check_path_permission_admin(self, db_session):
        """Test that admins have unrestricted access."""
        admin = User(username="admin", roles=["admin"])
        # Should not raise
        check_path_permission(db_session, admin, Path("/any/path"))

    def test_check_path_permission_viewer_denied(self, db_session):
        """Test that viewers are denied arbitrary access."""
        viewer = User(username="viewer", roles=["viewer"])
        with pytest.raises(HTTPException) as exc:
            check_path_permission(db_session, viewer, Path("/etc/passwd"))
        assert exc.value.status_code == 403

    def test_check_path_permission_viewer_allowed(self, db_session, tmp_path):
        """Test that viewers can access allowed paths."""
        hot_dir = tmp_path / "allowed_hot"
        hot_dir.mkdir()
        db_session.add(MonitoredPath(name="Allowed", source_path=str(hot_dir)))
        db_session.commit()
        
        viewer = User(username="viewer", roles=["viewer"])
        # Should not raise
        check_path_permission(db_session, viewer, hot_dir / "file.txt")

    def test_check_path_permission_invalid_db_paths(self, db_session, tmp_path):
        """Test resilience against invalid paths in database."""
        # Add invalid path containing null byte (illegal in most filesystems)
        db_session.add(MonitoredPath(name="BadPath", source_path="\0invalid", operation_type="move"))
        db_session.add(ColdStorageLocation(name="BadCold", path="\0invalid_cold"))
        db_session.commit()
        
        viewer = User(username="viewer", roles=["viewer"])
        # Should still work (skip bad paths) and deny access to other paths
        with pytest.raises(HTTPException) as exc:
            check_path_permission(db_session, viewer, Path("/tmp"))
        assert exc.value.status_code == 403
