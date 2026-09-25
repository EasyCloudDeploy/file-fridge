"""Extended file reconciliation coverage."""

import pytest

from app.models import OperationType, StorageType
from app.services.file_reconciliation import FileReconciliation


@pytest.mark.unit
class TestFileReconciliationExtended:
    def test_reconcile_wrong_symlink_target(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot_wrong"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold_wrong"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        cold_file = cold_dir / "data.txt"
        cold_file.write_text("cold")
        wrong_target = cold_dir / "other.txt"
        wrong_target.write_text("other")

        link = hot_dir / "data.txt"
        link.symlink_to(wrong_target)

        inv = file_inventory_factory(
            path=str(cold_file),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        path = db_session.get(
            __import__("app.models", fromlist=["MonitoredPath"]).MonitoredPath, inv.path_id
        )
        path.operation_type = OperationType.SYMLINK
        path.source_path = str(hot_dir)
        db_session.commit()

        stats = FileReconciliation.reconcile_missing_symlinks(path, db_session)
        assert stats["symlinks_skipped"] >= 1
        assert link.resolve() == wrong_target

    def test_reconcile_missing_cold_file_adds_error(
        self, db_session, tmp_path, file_inventory_factory, storage_location
    ):
        hot_dir = tmp_path / "hot_missing_cold"
        hot_dir.mkdir()
        cold_dir = tmp_path / "cold_missing_cold"
        cold_dir.mkdir()
        storage_location.path = str(cold_dir)
        db_session.commit()

        missing_cold = cold_dir / "missing-on-disk.txt"
        inv = file_inventory_factory(
            path=str(missing_cold),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
            create_physical_file=False,
            path_name="Missing Cold File Path",
        )
        from app.models import MonitoredPath

        path = db_session.get(MonitoredPath, inv.path_id)
        path.operation_type = OperationType.SYMLINK
        path.source_path = str(hot_dir)
        path.storage_locations = [storage_location]
        db_session.commit()

        stats = FileReconciliation.reconcile_missing_symlinks(path, db_session)
        assert stats["symlinks_checked"] == 1
        assert stats["errors"]
        assert "missing-on-disk.txt" in stats["errors"][0]
