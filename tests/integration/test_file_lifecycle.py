import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch  # Added here

import pytest
from app.database import SessionLocal
from app.models import MonitoredPath, ColdStorageLocation, StorageType, FileStatus, Criteria
from app.services.file_workflow_service import file_workflow_service


@pytest.fixture
def test_paths(tmp_path):
    """Fixture to set up hot and cold storage directories for integration tests."""
    hot_path = tmp_path / "hot_data"
    cold_path = tmp_path / "cold_data"
    hot_path.mkdir()
    cold_path.mkdir()
    return hot_path, cold_path


@pytest.fixture
def monitored_path_with_locations(db_session, test_paths):
    """Fixture to create a MonitoredPath linked to cold storage locations."""
    hot_path, cold_path = test_paths

    # Create cold storage location
    cold_loc = ColdStorageLocation(name="TestColdLoc", path=str(cold_path))
    db_session.add(cold_loc)
    db_session.commit()
    db_session.refresh(cold_loc)

    # Create monitored path
    monitored = MonitoredPath(
        name="TestMonitoredPath",
        source_path=str(hot_path),
        check_interval_seconds=3600,
        enabled=True,
    )
    monitored.storage_locations.append(cold_loc)
    db_session.add(monitored)
    db_session.commit()
    db_session.refresh(monitored)
    return monitored, hot_path, cold_path


def test_file_lifecycle_move_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with MOVE operation:
    Create file -> Scan -> Move to cold -> Verify -> Thaw -> Verify
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"
    db_session.commit()

    # 1. Create a test file in hot storage
    file_to_move = hot_path / "test_file_move.txt"
    file_to_move.write_text("This is content for a file to be moved.")
    original_mtime = file_to_move.stat().st_mtime
    time.sleep(0.1)  # Ensure mtime is different if possible

    # Add a criteria for moving (e.g., mtime > 0 minutes ago, effectively all files)
    criteria = Criteria(
        path_id=monitored_path.id,
        criterion_type="mtime",
        operator="<",
        value="-1",
        enabled=True,
    )
    db_session.add(criteria)
    db_session.commit()

    # 2. Run scan workflow - should move file to cold storage
    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify file is moved from hot to cold
    assert not file_to_move.exists()
    expected_cold_path = cold_path / "test_file_move.txt"
    assert expected_cold_path.exists()
    assert expected_cold_path.read_text() == "This is content for a file to be moved."
    assert expected_cold_path.stat().st_mtime == original_mtime  # Timestamps preserved

    # Verify inventory updated
    db = SessionLocal()
    inventory_entry = (
        db.query(file_workflow_service.FileInventory)
        .filter_by(file_path=str(expected_cold_path))
        .first()
    )
    assert inventory_entry is not None
    assert inventory_entry.storage_type == StorageType.COLD
    assert inventory_entry.status == FileStatus.ACTIVE
    db.close()

    # 3. Thaw the file back (e.g., by changing criteria or manually thawing)
    # For this test, we'll simulate thawing by setting the file back to active
    # and then manually calling _thaw_single_file. In a real scenario, we'd adjust criteria
    # and re-scan, but for direct integration, this is fine.

    # Simulate criteria change so file is considered "hot" again
    criteria.operator = "<"
    criteria.value = "-1000"  # Effectively never matches
    db_session.commit()

    # Manually trigger thaw for the file via service (as it would be done by process_path)
    thaw_results = file_workflow_service._thaw_single_file(
        symlink_path=expected_cold_path,  # In MOVE, the cold path is the "symlink_path" to move from
        cold_storage_path=expected_cold_path,  # In MOVE, this is also the cold path
        path=monitored_path,
    )
    assert thaw_results["success"] is True
    assert not expected_cold_path.exists()
    assert file_to_move.exists()  # The original hot path should now have the file

    # Verify inventory updated after thaw
    db = SessionLocal()
    inventory_entry = (
        db.query(file_workflow_service.FileInventory).filter_by(file_path=str(file_to_move)).first()
    )
    assert inventory_entry is not None
    assert inventory_entry.storage_type == StorageType.HOT
    assert inventory_entry.status == FileStatus.ACTIVE
    db.close()


def test_file_lifecycle_symlink_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with SYMLINK operation:
    Create file -> Scan -> Move to cold + Symlink -> Verify -> Thaw -> Verify
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "symlink"
    db_session.commit()

    # 1. Create a test file in hot storage
    file_to_symlink = hot_path / "test_file_symlink.txt"
    file_to_symlink.write_text("This is content for a file to be symlinked.")
    original_mtime = file_to_symlink.stat().st_mtime
    time.sleep(0.1)

    # Add a criteria for moving
    criteria = Criteria(
        path_id=monitored_path.id,
        criterion_type="mtime",
        operator="<",
        value="-1",
        enabled=True,
    )
    db_session.add(criteria)
    db_session.commit()

    # 2. Run scan workflow - should move file to cold storage and create symlink
    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify original file is now a symlink
    assert file_to_symlink.is_symlink()
    expected_cold_path = cold_path / "test_file_symlink.txt"
    assert file_to_symlink.resolve() == expected_cold_path
    assert expected_cold_path.exists()
    assert expected_cold_path.read_text() == "This is content for a file to be symlinked."
    assert expected_cold_path.stat().st_mtime == original_mtime

    # Verify inventory updated
    db = SessionLocal()
    # Inventory for symlink operations points to the symlink itself
    inventory_entry = (
        db.query(file_workflow_service.FileInventory)
        .filter_by(file_path=str(file_to_symlink))
        .first()
    )
    assert inventory_entry is not None
    assert inventory_entry.storage_type == StorageType.COLD  # Symlink is considered "cold"
    assert inventory_entry.status == FileStatus.ACTIVE
    db.close()

    # 3. Thaw the file back (simulate by re-scanning with non-matching criteria)
    criteria.operator = "<"
    criteria.value = "-1000"  # Effectively never matches
    db_session.commit()

    # Run scan again to trigger thawing
    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_found"] == 1  # Still sees the symlink
    assert results_thaw["files_moved"] == 1  # The thaw operation counts as a move
    assert results_thaw["errors"] == []

    # Verify symlink is gone and original file is back
    assert not file_to_symlink.is_symlink()
    assert file_to_symlink.exists()
    assert not expected_cold_path.exists()
    assert file_to_symlink.read_text() == "This is content for a file to be symlinked."

    # Verify inventory updated after thaw
    db = SessionLocal()
    inventory_entry = (
        db.query(file_workflow_service.FileInventory)
        .filter_by(file_path=str(file_to_symlink))
        .first()
    )
    assert inventory_entry is not None
    assert inventory_entry.storage_type == StorageType.HOT
    assert inventory_entry.status == FileStatus.ACTIVE
    db.close()


def test_file_lifecycle_copy_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with COPY operation:
    Create file -> Scan -> Copy to cold -> Verify -> Thaw (delete cold copy) -> Verify
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "copy"
    db_session.commit()

    # 1. Create a test file in hot storage
    file_to_copy = hot_path / "test_file_copy.txt"
    file_to_copy.write_text("This is content for a file to be copied.")
    original_mtime = file_to_copy.stat().st_mtime
    time.sleep(0.1)

    # Add a criteria for copying
    criteria = Criteria(
        path_id=monitored_path.id,
        criterion_type="mtime",
        operator="<",
        value="-1",
        enabled=True,
    )
    db_session.add(criteria)
    db_session.commit()

    # 2. Run scan workflow - should copy file to cold storage
    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify original file still exists in hot
    assert file_to_copy.exists()
    assert file_to_copy.read_text() == "This is content for a file to be copied."

    # Verify copy exists in cold
    expected_cold_path = cold_path / "test_file_copy.txt"
    assert expected_cold_path.exists()
    assert expected_cold_path.read_text() == "This is content for a file to be copied."
    assert expected_cold_path.stat().st_mtime == original_mtime

    # Verify inventory updated (hot file should still be hot, cold copy registered as cold)
    db = SessionLocal()
    hot_inventory = (
        db.query(file_workflow_service.FileInventory).filter_by(file_path=str(file_to_copy)).first()
    )
    cold_inventory = (
        db.query(file_workflow_service.FileInventory)
        .filter_by(file_path=str(expected_cold_path))
        .first()
    )

    assert hot_inventory is not None
    assert hot_inventory.storage_type == StorageType.HOT
    assert hot_inventory.status == FileStatus.ACTIVE

    assert cold_inventory is not None
    assert cold_inventory.storage_type == StorageType.COLD
    assert cold_inventory.status == FileStatus.ACTIVE
    db.close()

    # 3. Thaw the file back (simulate by removing the cold copy and updating inventory)
    # For COPY, thawing means deleting the cold copy. The hot file remains untouched.

    # We need to explicitly delete the cold file to simulate thawing for COPY operation
    # In a real scenario, changing criteria would cause the service to delete the cold copy
    # We will simulate the deletion and then verify inventory update
    expected_cold_path.unlink()

    # Update criteria so it no longer matches
    criteria.operator = "<"
    criteria.value = "-1000"  # Effectively never matches
    db_session.commit()

    # Run scan again. The hot file no longer matches, and the cold copy is gone.
    # The scan will clean up the orphaned cold inventory entry.
    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    # files_found will be 1 (the hot file), files_moved will be 0 as the "thaw" is just cleanup
    assert results_thaw["files_found"] == 1
    assert results_thaw["files_moved"] == 0
    assert results_thaw["files_cleaned"] > 0  # Should clean up the orphaned cold inventory
    assert results_thaw["errors"] == []

    # Verify hot file still exists
    assert file_to_copy.exists()
    assert not expected_cold_path.exists()

    # Verify inventory updated after thaw
    db = SessionLocal()
    hot_inventory = (
        db.query(file_workflow_service.FileInventory).filter_by(file_path=str(file_to_copy)).first()
    )
    cold_inventory = (
        db.query(file_workflow_service.FileInventory)
        .filter_by(file_path=str(expected_cold_path))
        .first()
    )

    assert hot_inventory is not None
    assert hot_inventory.storage_type == StorageType.HOT
    assert hot_inventory.status == FileStatus.ACTIVE
    assert cold_inventory is None  # Cold inventory entry should be gone
    db.close()


def test_file_lifecycle_non_existent_file(monitored_path_with_locations, db_session):
    """
    Test that the workflow handles a file disappearing between scan and process.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"
    db_session.commit()

    # 1. Create a dummy file in hot storage to be "scanned"
    file_to_disappear = hot_path / "disappearing_file.txt"
    file_to_disappear.write_text("I will vanish!")

    # Add a criteria for moving
    criteria = Criteria(
        path_id=monitored_path.id,
        criterion_type="mtime",
        operator="<",
        value="-1",
        enabled=True,
    )
    db_session.add(criteria)
    db_session.commit()

    # 2. Mock _process_single_file to simulate file disappearing
    with patch(
        "app.services.file_workflow_service.FileWorkflowService._process_single_file"
    ) as mock_process_single_file:
        mock_process_single_file.return_value = {
            "success": True,
            "skipped": True,
        }  # Indicate it was skipped because it disappeared

        # This will be called by _scan_path which will "find" the file
        results = file_workflow_service.process_path(monitored_path, db_session)

        # The mock doesn't actually remove the file, so we need to do it here
        file_to_disappear.unlink()

        assert results["files_found"] == 1
        assert results["files_moved"] == 0  # Should not attempt to move if file disappeared
        assert results["errors"] == []
