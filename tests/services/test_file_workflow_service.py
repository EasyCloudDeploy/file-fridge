import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call, ANY

import pytest
from app.models import (
    MonitoredPath,
    Criteria,
    CriterionType,
    Operator,
    FileInventory,
    FileStatus,
    StorageType,
    ScanStatus,
    ColdStorageLocation,
    FileRecord,
)
from app.services.checksum_verifier import checksum_verifier
from app.services.file_freezer import FileFreezer
from app.services.file_thawer import FileThawer
from app.services.file_workflow_service import FileWorkflowService


@pytest.fixture
def monitored_path(db_session):
    """Fixture for a MonitoredPath object."""
    path = MonitoredPath(
        name="Test Path",
        source_path="/tmp/hot",
        operation_type="move",
        last_scan_status=ScanStatus.SUCCESS,
    )
    db_session.add(path)
    db_session.commit()
    db_session.refresh(path)
    return path


@pytest.fixture
def file_inventory(db_session, monitored_path):
    """Fixture for a FileInventory object."""

    def _create_inventory(file_path, storage_type, status):
        inventory = FileInventory(
            path_id=monitored_path.id,
            file_path=str(file_path),
            storage_type=storage_type,
            status=status,
            file_size=1024,
            file_mtime=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        )
        db_session.add(inventory)
        db_session.commit()
        db_session.refresh(inventory)
        return inventory

    return _create_inventory


@pytest.fixture
def encrypted_cold_storage(db_session, tmp_path):
    """Fixture for an encrypted ColdStorageLocation."""
    cold_storage_path = tmp_path / "cold_encrypted"
    cold_storage_path.mkdir()
    location = ColdStorageLocation(
        name="Encrypted Cold Storage",
        path=str(cold_storage_path),
        is_encrypted=True,
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_path_scan_already_running(mock_scan_progress, monitored_path, db_session):
    """Test process_path skips if a scan is already running."""
    mock_scan_progress.start_scan.return_value = ("scan123", False)

    service = FileWorkflowService()
    result = service.process_path(monitored_path, db_session)

    assert result["scan_skipped"] is True
    assert "already running" in result["scan_skipped_reason"]
    mock_scan_progress.start_scan.assert_called_once_with(monitored_path.id, total_files=0)


@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_path_in_error_state(mock_scan_progress, monitored_path, db_session):
    """Test process_path handles a path in an error state."""
    mock_scan_progress.start_scan.return_value = ("scan123", True)
    monitored_path.error_message = "Disk is full"
    db_session.commit()

    service = FileWorkflowService()
    result = service.process_path(monitored_path, db_session)

    assert "Path is in error state" in result["errors"][0]
    mock_scan_progress.finish_scan.assert_called_once_with(monitored_path.id, status="failed")
    db_session.refresh(monitored_path)
    assert monitored_path.last_scan_status == ScanStatus.FAILURE


@patch("app.services.file_workflow_service.FileCleanup.cleanup_missing_files")
@patch("app.services.file_workflow_service.FileCleanup.cleanup_duplicates")
@patch("app.services.file_workflow_service.FileCleanup.cleanup_symlink_inventory_entries")
@patch("app.services.file_workflow_service.FileReconciliation.reconcile_missing_symlinks")
@patch("app.services.file_workflow_service.FileWorkflowService._scan_path")
@patch("app.services.file_workflow_service.FileWorkflowService._process_single_file")
@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_path_main_workflow(
    mock_scan_progress,
    mock_process_single_file,
    mock_scan_path,
    mock_reconcile,
    mock_cleanup_symlinks,
    mock_cleanup_duplicates,
    mock_cleanup_missing,
    monitored_path,
    db_session,
):
    """Test the main success workflow of process_path."""
    mock_scan_progress.start_scan.return_value = ("scan123", True)
    mock_cleanup_missing.return_value = {"removed": 1, "errors": []}
    mock_cleanup_duplicates.return_value = {"removed": 1, "errors": []}
    mock_cleanup_symlinks.return_value = {"removed": 1, "errors": []}
    mock_reconcile.return_value = {"symlinks_created": 1, "errors": []}

    file_to_move = Path("/tmp/hot/file1.txt")
    mock_scan_path.return_value = {
        "to_cold": [(file_to_move, [1])],
        "to_hot": [],
        "inventory_updated": 10,
        "skipped_hot": 5,
        "skipped_cold": 2,
        "total_scanned": 17,
    }

    mock_process_single_file.return_value = {"success": True}

    service = FileWorkflowService()

    with patch("app.services.file_workflow_service.ThreadPoolExecutor") as mock_executor:
        # This makes the executor run tasks sequentially in the test
        mock_executor.return_value.__enter__.return_value.submit = lambda fn, *args, **kwargs: MagicMock(
            result=lambda: fn(*args, **kwargs)
        )

        result = service.process_path(monitored_path, db_session)

    assert result["files_found"] == 1
    assert result["files_moved"] == 1
    assert result["files_cleaned"] == 3
    assert result["errors"] == []
    assert (
        db_session.query(MonitoredPath).get(monitored_path.id).last_scan_status
        == ScanStatus.SUCCESS
    )

    mock_scan_path.assert_called_once_with(monitored_path, db_session)
    mock_process_single_file.assert_called_once_with(file_to_move, [1], monitored_path)


@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """Test the _scan_path method."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    # This test assumes a single, non-encrypted cold storage path for simplicity
    monitored_path.storage_locations = [
        ColdStorageLocation(name="Default Cold", path=str(cold_path))
    ]

    # File that should be moved to cold
    file_to_freeze = hot_path / "old_file.txt"
    file_to_freeze.touch()

    # File that should stay in hot
    file_to_keep = hot_path / "new_file.txt"
    file_to_keep.touch()

    # Symlink to a file in cold storage that should be thawed
    symlink_to_thaw = hot_path / "thaw_me.txt"
    cold_file_for_thaw = cold_path / "thaw_me.txt"
    cold_file_for_thaw.touch()
    symlink_to_thaw.symlink_to(cold_file_for_thaw)

    # Mock scandir to return our test files
    mock_scandir.side_effect = [
        # First call for hot path
        [
            MagicMock(
                path=str(file_to_freeze),
                is_symlink=lambda: False,
                stat=lambda **kw: file_to_freeze.stat(),
            ),
            MagicMock(
                path=str(file_to_keep), is_symlink=lambda: False, stat=lambda **kw: file_to_keep.stat()
            ),
            MagicMock(
                path=str(symlink_to_thaw),
                is_symlink=lambda: True,
                stat=lambda **kw: symlink_to_thaw.lstat(),
            ),
        ],
        # Second call for cold path
        [
            MagicMock(
                path=str(cold_file_for_thaw),
                is_symlink=lambda: False,
                stat=lambda **kw: cold_file_for_thaw.stat(),
            ),
        ],
    ]

    # Mock CriteriaMatcher to control which files match
    def match_file_side_effect(file_path, criteria, actual_file_path):
        if file_path == file_to_freeze:
            return False, []  # Not active -> move to cold
        if file_path == file_to_keep:
            return True, [1]  # Active -> keep in hot
        if file_path == symlink_to_thaw:
            return True, [2]  # Active -> thaw from cold
        return True, []

    mock_match_file.side_effect = match_file_side_effect

    service = FileWorkflowService()
    result = service._scan_path(monitored_path, db_session)

    assert result["to_cold"] == [(file_to_freeze, [])]
    assert result["to_hot"] == [(symlink_to_thaw, cold_file_for_thaw)]
    assert result["skipped_hot"] == 1
    assert result["skipped_cold"] == 0

    mock_update_inventory.assert_called_once()


@patch("app.services.file_workflow_service.FileMover.move_with_rollback")
@patch("app.services.file_workflow_service.storage_routing_service.select_storage_location")
@patch("app.services.file_workflow_service.checksum_verifier.calculate_checksum")
@patch("app.services.file_workflow_service.audit_trail_service")
@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_single_file(
    mock_scan_progress,
    mock_audit_trail,
    mock_checksum,
    mock_select_location,
    mock_move,
    monitored_path,
    file_inventory,
    tmp_path,
):
    """Test the _process_single_file method for a successful move."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    file_to_move = hot_path / "file.txt"
    file_to_move.write_text("content")

    inventory = file_inventory(file_to_move, StorageType.HOT, FileStatus.ACTIVE)

    mock_select_location.return_value = MagicMock(id=1, path=str(cold_path))
    mock_move.return_value = (True, None, "checksum_after")
    mock_checksum.return_value = "checksum_before"

    service = FileWorkflowService()
    result = service._process_single_file(file_to_move, [1], monitored_path)

    assert result["success"] is True

    # Reload inventory from a new session to check committed state
    from app.database import SessionLocal

    db = SessionLocal()
    reloaded_inventory = db.query(FileInventory).get(inventory.id)
    assert reloaded_inventory.storage_type == StorageType.COLD
    assert reloaded_inventory.file_path == str(cold_path / "file.txt")

    mock_audit_trail.log_freeze_operation.assert_called_once()


def test_freeze_and_thaw_encrypted_file(db_session, monitored_path, encrypted_cold_storage, tmp_path):
    """
    Test the full freeze (with encryption) and thaw (with decryption) cycle.
    """
    # 1. Setup
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    monitored_path.source_path = str(hot_path)

    # Associate encrypted storage with the monitored path
    monitored_path.storage_locations.append(encrypted_cold_storage)
    db_session.commit()

    test_file = hot_path / "secret_document.txt"
    original_content = "This is a secret document."
    test_file.write_bytes(original_content.encode())

    original_checksum = checksum_verifier.calculate_checksum(test_file)

    # Create a FileInventory entry for the hot file
    inventory_entry = FileInventory(
        path_id=monitored_path.id,
        file_path=str(test_file),
        storage_type=StorageType.HOT,
        status=FileStatus.ACTIVE,
        file_size=test_file.stat().st_size,
        file_mtime=datetime.fromtimestamp(test_file.stat().st_mtime, tz=timezone.utc),
    )
    db_session.add(inventory_entry)
    db_session.commit()
    db_session.refresh(inventory_entry)

    # 2. Freeze the file
    freeze_success, freeze_error, cold_path_str = FileFreezer.freeze_file(
        file=inventory_entry,
        monitored_path=monitored_path,
        storage_location=encrypted_cold_storage,
        db=db_session,
    )

    assert freeze_success is True
    assert freeze_error is None
    assert cold_path_str.endswith(".ffenc")

    cold_path = Path(cold_path_str)
    assert not test_file.exists()
    assert cold_path.exists()

    # Verify file content is encrypted (is not the same as original)
    assert cold_path.read_bytes() != original_content.encode()

    # Verify inventory is updated
    db_session.refresh(inventory_entry)
    assert inventory_entry.storage_type == StorageType.COLD
    assert inventory_entry.is_encrypted is True
    assert inventory_entry.file_path == str(cold_path)

    # 3. Thaw the file
    file_record = (
        db_session.query(FileRecord).filter(FileRecord.cold_storage_path == str(cold_path)).first()
    )
    assert file_record is not None

    thaw_success, thaw_error = FileThawer.thaw_file(file_record, db=db_session)

    assert thaw_success is True
    assert thaw_error is None

    assert not cold_path.exists()
    assert test_file.exists()

    # 4. Verify contents
    thawed_checksum = checksum_verifier.calculate_checksum(test_file)
    assert thawed_checksum == original_checksum
    assert test_file.read_bytes() == original_content.encode()

    # Verify inventory is updated
    db_session.refresh(inventory_entry)
    assert inventory_entry.storage_type == StorageType.HOT
    assert inventory_entry.is_encrypted is False
    assert inventory_entry.file_path == str(test_file)