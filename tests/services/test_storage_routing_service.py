from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import ColdStorageLocation
from app.services.storage_routing_service import storage_routing_service


@pytest.mark.unit
class TestStorageRoutingService:
    def test_select_storage_location_success(
        self, db_session, monitored_path_factory, storage_location
    ):
        """Test basic selection of storage location."""
        path = monitored_path_factory("Route Success", "/tmp/hot_route")
        # Ensure storage_location path exists and has space
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        # Mock disk usage to ensure enough space
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            loc = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert loc is not None
            assert loc.id == storage_location.id

    def test_select_storage_location_multiple_scoring(
        self, db_session, monitored_path_factory, tmp_path
    ):
        """Test selection between multiple locations based on scoring."""
        loc1_path = tmp_path / "loc1"
        loc1_path.mkdir()
        loc1 = ColdStorageLocation(name="Loc 1", path=str(loc1_path))

        loc2_path = tmp_path / "loc2"
        loc2_path.mkdir()
        loc2 = ColdStorageLocation(name="Loc 2", path=str(loc2_path))

        db_session.add_all([loc1, loc2])
        db_session.commit()

        path = monitored_path_factory("Multi Route", "/tmp/hot_multi")
        path.storage_locations = [loc1, loc2]
        db_session.commit()

        # Mock disk usage to favor loc2 (more free space)
        def mock_usage(p):
            if str(p) == str(loc1_path):
                # 2GB free (just above minimum 1GB)
                return MagicMock(total=100 * 10**9, used=98 * 10**9, free=2 * 10**9)
            # 50GB free
            return MagicMock(total=100 * 10**9, used=50 * 10**9, free=50 * 10**9)

        with patch("shutil.disk_usage", side_effect=mock_usage):
            selected = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert selected is not None
            assert selected.id == loc2.id

    def test_select_storage_location_insufficient_space(
        self, db_session, monitored_path_factory, storage_location
    ):
        """Test failure when no location has enough space."""
        path = monitored_path_factory("No Space Path", "/tmp/hot_nospace")
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        # Mock disk usage to return very low space
        with patch(
            "shutil.disk_usage", return_value=MagicMock(total=10**9, used=10**9 - 1, free=1)
        ):
            loc = storage_routing_service.select_storage_location(db_session, path, 1024)
            assert loc is None

    def test_has_sufficient_space(self, storage_location):
        """Test the has_sufficient_space helper."""
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            assert storage_routing_service.has_sufficient_space(storage_location, 1024) is True

        with patch(
            "shutil.disk_usage", return_value=MagicMock(total=10**9, used=10**9 - 1, free=1)
        ):
            assert storage_routing_service.has_sufficient_space(storage_location, 1024) is False

    def test_get_location_health(self, db_session, storage_location):
        """Test retrieving location health metrics."""
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            health = storage_routing_service.get_location_health(db_session, storage_location, 1)
            assert health["healthy"] is True
            assert health["free_space_bytes"] == 10**11

    def test_get_location_health_not_accessible(self, db_session, storage_location):
        """Test health metrics for non-accessible location."""
        storage_location.path = "/non/existent/storage/path"

        health = storage_routing_service.get_location_health(db_session, storage_location, 1)
        assert health["healthy"] is False
        assert health["reason"]  # backend validation returns a non-empty reason string

    def test_get_location_health_with_transactions(self, db_session, storage_location):
        """Test that only failed transactions are counted as errors in health/routing, verifying C1 fix."""
        from datetime import datetime, timezone

        from app.models import FileInventory, FileTransactionHistory

        Path(storage_location.path).mkdir(parents=True, exist_ok=True)

        # Create a FileInventory record first
        file_record = FileInventory(
            path_id=1,
            file_path="/tmp/hot/test.txt",
            file_size=1024,
            file_mtime=datetime.now(tz=timezone.utc),
            storage_type="hot",
            status="active",
        )
        db_session.add(file_record)
        db_session.commit()

        # Add a successful transaction
        tx_success = FileTransactionHistory(
            file_id=file_record.id,
            new_storage_location_id=storage_location.id,
            success=True,
            created_at=datetime.now(tz=timezone.utc),
            transaction_type="freeze",
        )
        # Add a failed transaction
        tx_fail = FileTransactionHistory(
            file_id=file_record.id,
            new_storage_location_id=storage_location.id,
            success=False,
            created_at=datetime.now(tz=timezone.utc),
            transaction_type="freeze",
        )
        db_session.add_all([tx_success, tx_fail])
        db_session.commit()

        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            health = storage_routing_service.get_location_health(db_session, storage_location, 1)
            # recent_errors should be 1 (only the failed one), NOT 2 (both)
            assert health["recent_errors"] == 1

    def test_select_storage_location_no_locations_configured(
        self, db_session, monitored_path_factory
    ):
        path = monitored_path_factory("No Storage", "/tmp/no_storage_hot")
        path.storage_locations = []
        db_session.commit()
        assert storage_routing_service.select_storage_location(db_session, path, 100) is None

    def test_select_storage_location_skips_paused(
        self, db_session, monitored_path_factory, storage_location, tmp_path
    ):
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        storage_location.paused = True
        db_session.commit()
        path = monitored_path_factory("Paused Route", "/tmp/paused_route_hot")
        with patch("shutil.disk_usage", return_value=MagicMock(total=10**12, used=0, free=10**11)):
            assert storage_routing_service.select_storage_location(db_session, path, 1024) is None

    def test_select_storage_location_remote_backend_without_disk_stats(
        self, db_session, monitored_path_factory, tmp_path
    ):
        from app.models import ColdStorageBackendType

        remote = ColdStorageLocation(
            name="Remote Route",
            path="s3://remote-route",
            backend_type=ColdStorageBackendType.S3,
        )
        remote.set_backend_config({"bucket": "remote-route"})
        db_session.add(remote)
        db_session.commit()
        path = monitored_path_factory("Remote Route Path", "/tmp/remote_route_hot")
        path.storage_locations = [remote]
        db_session.commit()
        selected = storage_routing_service.select_storage_location(db_session, path, 1024)
        assert selected is not None
        assert selected.id == remote.id

    def test_get_location_health_low_space_and_recent_errors(
        self, db_session, monitored_path_factory, storage_location, tmp_path
    ):
        from datetime import datetime, timezone

        from app.models import FileInventory, FileTransactionHistory

        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        path = monitored_path_factory("Health Path", "/tmp/health_hot")
        inv = FileInventory(
            path_id=path.id,
            file_path="/cold/x.bin",
            file_size=1,
            file_mtime=datetime.now(tz=timezone.utc),
            storage_type="cold",
            cold_storage_location_id=storage_location.id,
        )
        db_session.add(inv)
        db_session.flush()
        db_session.add(
            FileTransactionHistory(
                file_id=inv.id,
                new_storage_location_id=storage_location.id,
                success=False,
                created_at=datetime.now(tz=timezone.utc),
                transaction_type="freeze",
            )
        )
        db_session.commit()

        with patch(
            "shutil.disk_usage", return_value=MagicMock(total=10**9, used=10**9 - 1, free=1)
        ):
            health = storage_routing_service.get_location_health(
                db_session, storage_location, path.id
            )
        assert health["healthy"] is False
        assert health["reason"] in ("Low space", "Recent errors")

    def test_has_sufficient_space_invalid_location(self, storage_location, monkeypatch):
        mock_backend = MagicMock()
        mock_backend.validate_location.return_value = (False, "missing")
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend", lambda _loc: mock_backend
        )
        assert storage_routing_service.has_sufficient_space(storage_location, 1024) is False

    def test_has_sufficient_space_remote_backend(self, db_session, monkeypatch):
        from app.models import ColdStorageBackendType

        remote = ColdStorageLocation(
            name="Remote Space",
            path="s3://space-bucket",
            backend_type=ColdStorageBackendType.S3,
        )
        remote.set_backend_config({"bucket": "space-bucket"})
        mock_backend = MagicMock()
        mock_backend.validate_location.return_value = (True, None)
        mock_backend.capabilities.return_value.supports_local_path_stats = False
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend", lambda _loc: mock_backend
        )
        assert storage_routing_service.has_sufficient_space(remote, 10**12) is True

    def test_has_sufficient_space_returns_false_on_error(self, storage_location, monkeypatch):
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend",
            MagicMock(side_effect=RuntimeError("backend error")),
        )
        assert storage_routing_service.has_sufficient_space(storage_location, 1024) is False

    def test_select_storage_location_skips_invalid_backend(
        self, db_session, monitored_path_factory, storage_location, monkeypatch
    ):
        path = monitored_path_factory("Bad Backend", "/tmp/bad_backend_hot")
        mock_backend = MagicMock()
        mock_backend.validate_location.return_value = (False, "offline")
        mock_backend.capabilities.return_value.supports_local_path_stats = True
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend", lambda _loc: mock_backend
        )
        assert storage_routing_service.select_storage_location(db_session, path, 1024) is None

    def test_select_storage_location_handles_evaluate_exception(
        self, db_session, monitored_path_factory, storage_location, monkeypatch
    ):
        Path(storage_location.path).mkdir(parents=True, exist_ok=True)
        path = monitored_path_factory("Eval Exc", "/tmp/eval_exc_hot")
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        assert storage_routing_service.select_storage_location(db_session, path, 1024) is None

    def test_get_location_health_exception(self, db_session, storage_location, monkeypatch):
        monkeypatch.setattr(
            "app.services.storage_routing_service.get_backend",
            MagicMock(side_effect=RuntimeError("health boom")),
        )
        health = storage_routing_service.get_location_health(db_session, storage_location, 1)
        assert health["healthy"] is False
        assert "health boom" in health["reason"]
