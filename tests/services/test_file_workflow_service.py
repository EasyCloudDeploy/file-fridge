import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
)
from app.services.file_workflow_service import FileWorkflowService


@pytest.fixture
def monitored_path(db_session):
    """Fixture for a MonitoredPath object."""
    # Create cold storage location
    cold_loc = ColdStorageLocation(name="TestColdLoc", path="/tmp/cold")
    db_session.add(cold_loc)
    db_session.flush()  # Flush to get an ID for cold_loc before creating MonitoredPath

    path = MonitoredPath(
        name="Test Path",
        source_path="/tmp/hot",
        operation_type="move",
        last_scan_status=ScanStatus.SUCCESS,
    )
    path.storage_locations.append(cold_loc)  # Link the cold storage location
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
    mock_scan_progress.is_stop_requested.return_value = False
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

    def _resolved_future(fn, *args, **kwargs):
        """Return an already-resolved Future so as_completed() doesn't block."""
        f = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:
            f.set_exception(exc)
        return f

    with patch("app.services.file_workflow_service.ThreadPoolExecutor") as mock_executor:
        mock_executor.return_value.__enter__.return_value.submit = _resolved_future

        # Manually set max_concurrent_migrations
        monitored_path.max_concurrent_migrations = 2
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

    # Assert ThreadPoolExecutor was called with max_workers=1 (min of max_concurrent_migrations (2) and len(matching_files) (1))
    mock_executor.assert_called_with(max_workers=1)


@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_path_stop_requested(mock_scan_progress, monitored_path, db_session):
    """Test process_path when a stop has been requested."""
    mock_scan_progress.start_scan.return_value = ("scan123", True)
    mock_scan_progress.is_stop_requested.return_value = True

    service = FileWorkflowService()
    service.process_path(monitored_path, db_session)

    mock_scan_progress.finish_scan.assert_called_with(monitored_path.id, status="stopped")
    db_session.refresh(monitored_path)
    assert monitored_path.last_scan_status == ScanStatus.STOPPED


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
    monitored_path.storage_locations[0].path = str(cold_path)

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
                path=str(file_to_keep),
                is_symlink=lambda: False,
                stat=lambda **kw: file_to_keep.stat(),
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
    db_session,
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

    # Suppress close() so the shared session stays alive across internal calls
    original_close = db_session.close
    db_session.close = lambda: None
    try:
        with patch(
            "app.services.file_workflow_service.SessionFactory",
            side_effect=lambda: db_session,
        ):
            result = service._process_single_file(file_to_move, [1], monitored_path)
    finally:
        db_session.close = original_close

    assert result["success"] is True

    db_session.expire_all()
    reloaded_inventory = db_session.query(FileInventory).get(inventory.id)
    assert reloaded_inventory.storage_type == StorageType.COLD
    assert reloaded_inventory.file_path == str(cold_path / "file.txt")

    mock_audit_trail.log_freeze_operation.assert_called_once()
    mock_move.assert_called_once()
    move_args = mock_move.call_args
    assert move_args.args == (file_to_move, cold_path / "file.txt", monitored_path.operation_type)
    assert move_args.kwargs["verify_checksum"] is True
    assert callable(move_args.kwargs["progress_callback"])


@patch("app.services.file_workflow_service.FileMover.move_with_rollback")
@patch("app.services.file_workflow_service.storage_routing_service.select_storage_location")
@patch("app.services.file_workflow_service.checksum_verifier.calculate_checksum")
@patch("app.services.file_workflow_service.audit_trail_service")
@patch("app.services.file_workflow_service.scan_progress_manager")
def test_process_single_file_passes_callable_progress_callback_on_move(
    mock_scan_progress,
    mock_audit_trail,
    mock_checksum,
    mock_select_location,
    mock_move,
    monitored_path,
    file_inventory,
    db_session,
    tmp_path,
):
    """Test freeze moves pass a callable progress callback into the mover."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    file_to_move = hot_path / "file.txt"
    file_to_move.write_text("content")

    file_inventory(file_to_move, StorageType.HOT, FileStatus.ACTIVE)
    mock_select_location.return_value = MagicMock(id=1, path=str(cold_path))
    mock_checksum.return_value = "checksum_before"
    mock_audit_trail.log_freeze_operation.return_value = None
    mock_scan_progress.start_file_operation.return_value = "freeze-op-1"

    def move_side_effect(source, destination, operation_type, **kwargs):
        assert source == file_to_move
        assert destination == cold_path / "file.txt"
        assert operation_type == monitored_path.operation_type
        assert kwargs["verify_checksum"] is True
        assert callable(kwargs["progress_callback"])
        return True, None, "checksum_after"

    mock_move.side_effect = move_side_effect

    service = FileWorkflowService()

    original_close = db_session.close
    db_session.close = lambda: None
    try:
        with patch(
            "app.services.file_workflow_service.SessionFactory",
            side_effect=lambda: db_session,
        ):
            result = service._process_single_file(file_to_move, [1], monitored_path)
    finally:
        db_session.close = original_close

    assert result["success"] is True
    mock_move.assert_called_once()


@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.checksum_verifier.calculate_checksum")
@patch("app.services.file_workflow_service.audit_trail_service")
def test_thaw_single_file(
    mock_audit_trail,
    mock_checksum,
    mock_scan_progress,
    monitored_path,
    file_inventory,
    db_session,
    tmp_path,
):
    """Test the _thaw_single_file method for a successful thaw."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.storage_locations[0].path = str(cold_path)

    cold_file = cold_path / "file.txt"
    cold_file.write_text("content")
    symlink_path = hot_path / "file.txt"
    symlink_path.symlink_to(cold_file)

    inventory = file_inventory(cold_file, StorageType.COLD, FileStatus.ACTIVE)
    mock_scan_progress.start_file_operation.return_value = "thaw-op-1"

    mock_checksum.side_effect = ["checksum1", "checksum1"]

    service = FileWorkflowService()

    # The original symlink exists, we need to remove it before thawing
    if symlink_path.is_symlink():
        symlink_path.unlink()

    # Suppress close() so the shared session stays alive across internal calls
    original_close = db_session.close
    db_session.close = lambda: None
    try:
        with patch(
            "app.services.file_workflow_service.SessionFactory",
            side_effect=lambda: db_session,
        ):
            result = service._thaw_single_file(symlink_path, cold_file, monitored_path)
    finally:
        db_session.close = original_close

    assert result["success"] is True
    assert not cold_file.exists()
    assert symlink_path.exists() and not symlink_path.is_symlink()

    db_session.expire_all()
    reloaded_inventory = db_session.query(FileInventory).get(inventory.id)
    assert reloaded_inventory.storage_type == StorageType.HOT
    assert reloaded_inventory.file_path == str(symlink_path)

    mock_audit_trail.log_thaw_operation.assert_called_once()
    mock_scan_progress.start_file_operation.assert_called_once_with(
        monitored_path.id,
        symlink_path.name,
        "move_to_hot",
        len("content"),
        file_path=str(cold_file),
        destination_path=str(symlink_path),
    )
    mock_scan_progress.update_file_progress.assert_called_once_with(
        monitored_path.id, "thaw-op-1", len("content")
    )
    mock_scan_progress.complete_file_operation.assert_called_once_with(
        monitored_path.id, "thaw-op-1", "move_to_hot", success=True
    )


@patch("app.services.file_workflow_service.scan_progress_manager")
@patch("app.services.file_workflow_service.checksum_verifier.calculate_checksum")
@patch("app.services.file_workflow_service.audit_trail_service")
@patch("app.services.file_workflow_service.FileThawer._move_preserving_timestamps")
def test_thaw_single_file_uses_callable_progress_callback_for_fallback_move(
    mock_move_preserving_timestamps,
    mock_audit_trail,
    mock_checksum,
    mock_scan_progress,
    monitored_path,
    file_inventory,
    db_session,
    tmp_path,
):
    """Test thaw fallback move preserves a callable progress callback."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.storage_locations[0].path = str(cold_path)

    cold_file = cold_path / "file.txt"
    cold_file.write_text("content")
    symlink_path = hot_path / "file.txt"

    inventory = file_inventory(cold_file, StorageType.COLD, FileStatus.ACTIVE)
    mock_scan_progress.start_file_operation.return_value = "thaw-op-1"
    mock_checksum.side_effect = ["checksum1", "checksum1"]

    temp_destination = hot_path / "file.txt.tmp"

    def fallback_side_effect(source, destination, progress_callback=None):
        assert source == cold_file
        assert destination == symlink_path
        assert callable(progress_callback)
        temp_destination.write_text("content")
        progress_callback(len("content"))
        return temp_destination, cold_file.stat()

    mock_move_preserving_timestamps.side_effect = fallback_side_effect

    service = FileWorkflowService()

    original_close = db_session.close
    db_session.close = lambda: None
    original_rename = Path.rename

    def rename_side_effect(self, target):
        if self == cold_file and Path(target) == symlink_path:
            raise OSError("Cross-device link")
        return original_rename(self, target)

    try:
        with patch(
            "app.services.file_workflow_service.SessionFactory",
            side_effect=lambda: db_session,
        ), patch("pathlib.Path.rename", autospec=True, side_effect=rename_side_effect):
            result = service._thaw_single_file(symlink_path, cold_file, monitored_path)
    finally:
        db_session.close = original_close

    assert result["success"] is True
    assert not cold_file.exists()
    assert symlink_path.exists()
    assert not temp_destination.exists()

    db_session.expire_all()
    reloaded_inventory = db_session.query(FileInventory).get(inventory.id)
    assert reloaded_inventory.storage_type == StorageType.HOT
    assert reloaded_inventory.file_path == str(symlink_path)
    mock_move_preserving_timestamps.assert_called_once()


def test_recursive_scandir(tmp_path):
    """Test the recursive directory scanning utility."""
    # Setup nested structure
    root = tmp_path / "root"
    root.mkdir()
    (root / "f1.txt").touch()

    sub = root / "sub"
    sub.mkdir()
    (sub / "f2.txt").touch()

    # Ignored pattern
    (root / ".DS_Store").touch()

    service = FileWorkflowService()
    files = list(service._recursive_scandir(str(root)))

    # Should find f1.txt and f2.txt, but not .DS_Store
    names = [Path(f.path).name for f in files]
    assert "f1.txt" in names
    assert "f2.txt" in names
    assert ".DS_Store" not in names
    assert len(files) == 2


# ---------------------------------------------------------------------------
# Orphaned symlink handling when operation_type changes away from SYMLINK
# ---------------------------------------------------------------------------

@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path_move_op_deletes_orphaned_symlinks(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """Symlinks left over from a previous SYMLINK operation are deleted when the
    path has since been changed to MOVE, leaving the cold file untouched."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.operation_type = "move"
    monitored_path.storage_locations[0].path = str(cold_path)
    db_session.commit()

    # Cold file is the real copy; hot entry is an orphaned symlink from when
    # the operation type used to be SYMLINK.
    cold_file = cold_path / "movie.mkv"
    cold_file.write_text("cold content")
    orphaned_symlink = hot_path / "movie.mkv"
    orphaned_symlink.symlink_to(cold_file)

    mock_scandir.side_effect = [
        [
            MagicMock(
                path=str(orphaned_symlink),
                is_symlink=lambda: True,
                stat=lambda **kw: orphaned_symlink.lstat(),
            )
        ],
        [],  # cold scan
    ]

    service = FileWorkflowService()
    result = service._scan_path(monitored_path, db_session)

    # Symlink must be gone; cold file must be intact
    assert not orphaned_symlink.exists()
    assert cold_file.exists()

    # File should not appear in to_cold or to_hot — it was handled inline
    assert result["to_cold"] == []
    assert result["to_hot"] == []


@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path_copy_op_thaws_orphaned_symlinks_when_active(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """When operation_type is COPY and a symlink-to-cold exists, the file is thawed
    regardless of whether criteria say it is active or inactive. COPY always needs
    a real file in hot storage."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.operation_type = "copy"
    monitored_path.storage_locations[0].path = str(cold_path)
    db_session.commit()

    cold_file = cold_path / "doc.pdf"
    cold_file.write_text("cold content")
    symlink = hot_path / "doc.pdf"
    symlink.symlink_to(cold_file)

    mock_scandir.side_effect = [
        [
            MagicMock(
                path=str(symlink),
                is_symlink=lambda: True,
                stat=lambda **kw: symlink.lstat(),
            )
        ],
        [],  # cold scan
    ]

    # Criteria says file is active (recently used — should stay in hot)
    mock_match_file.return_value = (True, [])

    service = FileWorkflowService()
    result = service._scan_path(monitored_path, db_session)

    # Thaw queued so a real hot copy can be restored
    assert result["to_hot"] == [(symlink, cold_file)]
    assert result["to_cold"] == []


@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path_symlink_op_does_not_delete_normal_symlinks(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """When operation_type is SYMLINK, symlinks pointing to cold storage are the
    expected state and must never be deleted."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.operation_type = "symlink"
    monitored_path.storage_locations[0].path = str(cold_path)
    db_session.commit()

    cold_file = cold_path / "archive.zip"
    cold_file.write_text("cold content")
    symlink = hot_path / "archive.zip"
    symlink.symlink_to(cold_file)

    mock_scandir.side_effect = [
        [
            MagicMock(
                path=str(symlink),
                is_symlink=lambda: True,
                stat=lambda **kw: symlink.lstat(),
            )
        ],
        [],  # cold scan
    ]

    # Criteria says file is still cold (not active)
    mock_match_file.return_value = (False, [])

    service = FileWorkflowService()
    result = service._scan_path(monitored_path, db_session)

    # Symlink must survive
    assert symlink.exists()
    assert symlink.is_symlink()

    assert result["to_cold"] == []
    assert result["to_hot"] == []


@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path_move_op_unlink_failure_does_not_abort_scan(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """If deleting an orphaned symlink fails (e.g. permission error), the scan
    continues processing remaining files rather than crashing."""
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.operation_type = "move"
    monitored_path.storage_locations[0].path = str(cold_path)
    db_session.commit()

    cold_file = cold_path / "protected.mkv"
    cold_file.write_text("content")
    orphaned_symlink = hot_path / "protected.mkv"
    orphaned_symlink.symlink_to(cold_file)

    regular_file = hot_path / "normal.txt"
    regular_file.write_text("content")

    mock_scandir.side_effect = [
        [
            MagicMock(
                path=str(orphaned_symlink),
                is_symlink=lambda: True,
                stat=lambda **kw: orphaned_symlink.lstat(),
            ),
            MagicMock(
                path=str(regular_file),
                is_symlink=lambda: False,
                stat=lambda **kw: regular_file.stat(),
            ),
        ],
        [],  # cold scan
    ]

    # Regular file should be moved to cold
    mock_match_file.return_value = (False, [])

    service = FileWorkflowService()

    with patch.object(orphaned_symlink.__class__, "unlink", side_effect=OSError("permission denied")):
        # Should not raise — OSError is caught internally
        result = service._scan_path(monitored_path, db_session)

    # Regular file still queued for freezing
    assert (regular_file, []) in result["to_cold"]


@patch("app.services.file_workflow_service.CriteriaMatcher.match_file")
@patch("app.services.file_workflow_service.FileWorkflowService._recursive_scandir")
@patch("app.services.file_workflow_service.FileWorkflowService._update_file_inventory")
@patch("app.services.file_workflow_service.check_atime_availability", return_value=(True, None))
def test_scan_path_copy_op_thaws_orphaned_symlinks_when_inactive(
    mock_check_atime,
    mock_update_inventory,
    mock_scandir,
    mock_match_file,
    monitored_path,
    db_session,
    tmp_path,
):
    """When operation_type is COPY and a symlink-to-cold exists, the file must be
    thawed even when criteria say it is inactive (should be cold). COPY semantics
    require a real file in hot storage, so the two-step migration is:
      scan 1: thaw cold → hot (remove symlink, restore real file)
      scan 2: freeze hot → cold as a proper copy (hot copy kept)
    """
    hot_path = tmp_path / "hot"
    hot_path.mkdir()
    cold_path = tmp_path / "cold"
    cold_path.mkdir()

    monitored_path.source_path = str(hot_path)
    monitored_path.operation_type = "copy"
    monitored_path.storage_locations[0].path = str(cold_path)
    db_session.commit()

    cold_file = cold_path / "report.pdf"
    cold_file.write_text("cold content")
    orphaned_symlink = hot_path / "report.pdf"
    orphaned_symlink.symlink_to(cold_file)

    mock_scandir.side_effect = [
        [
            MagicMock(
                path=str(orphaned_symlink),
                is_symlink=lambda: True,
                stat=lambda **kw: orphaned_symlink.lstat(),
            )
        ],
        [],  # cold scan
    ]

    # Criteria says file is inactive (old enough to be cold) — the sticky case
    mock_match_file.return_value = (False, [])

    service = FileWorkflowService()
    result = service._scan_path(monitored_path, db_session)

    # Must be queued for thawing so the next scan can re-freeze it as a real copy
    assert result["to_hot"] == [(orphaned_symlink, cold_file)]
    assert result["to_cold"] == []
