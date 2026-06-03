
from unittest.mock import patch

import pytest

from app.models import (
    ColdStorageLocation,
    Criteria,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    StorageType,
)
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

    cold_loc = ColdStorageLocation(name="TestColdLoc", path=str(cold_path))
    db_session.add(cold_loc)
    db_session.commit()
    db_session.refresh(cold_loc)

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


def _never_match_criterion(path_id: int) -> Criteria:
    """Return a criterion that can never be satisfied (mtime < -1).

    Since file age is always >= 0, 'file_age_minutes < -1' is always False.
    is_active = False  →  file is moved to cold storage.
    """
    return Criteria(
        path_id=path_id,
        criterion_type="mtime",
        operator="<",
        value="-1",
        enabled=True,
    )


def _always_match_criterion(path_id: int) -> Criteria:
    """Return a criterion that is always satisfied (mtime > -1).

    Since file age is always >= 0, 'file_age_minutes > -1' is always True.
    is_active = True  →  file stays in hot storage (or is thawed back).
    """
    return Criteria(
        path_id=path_id,
        criterion_type="mtime",
        operator=">",
        value="-1",
        enabled=True,
    )


def test_file_lifecycle_move_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with MOVE operation.

    Phase 1 – Freeze:
        Create file → scan with never-matching criteria → file moves to cold.
    Phase 2 – Thaw:
        Change criteria to always-matching → scan → cold file moves back to hot.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"
    db_session.commit()

    # ---- Phase 1: Freeze ----
    file_to_move = hot_path / "test_file_move.txt"
    file_to_move.write_text("This is content for a file to be moved.")
    original_mtime = file_to_move.stat().st_mtime

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1, f"errors: {results['errors']}"
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify file system: file moved from hot to cold
    assert not file_to_move.exists()
    expected_cold_path = cold_path / "test_file_move.txt"
    assert expected_cold_path.exists()
    assert expected_cold_path.read_text() == "This is content for a file to be moved."
    assert expected_cold_path.stat().st_mtime == original_mtime  # timestamps preserved

    # Verify inventory: record now points to cold path, type = COLD
    db_session.expire_all()
    inv = db_session.query(FileInventory).filter_by(
        file_path=str(expected_cold_path)
    ).first()
    assert inv is not None
    assert inv.storage_type == StorageType.COLD
    assert inv.status == FileStatus.ACTIVE

    # ---- Phase 2: Thaw ----
    # Swap criteria so files should be kept hot → triggers thaw on next scan
    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    # The cold scan detects cold_file where hot counterpart is missing →
    # CriteriaMatcher.match_file uses cold file stat (actual_file_path) → is_active=True
    # → _thaw_single_file moves cold file back to hot path
    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_found"] == 0   # nothing to freeze
    assert results_thaw["files_moved"] == 1   # one thaw
    assert results_thaw["errors"] == []

    # Verify file system: file is back in hot
    assert file_to_move.exists()
    assert not expected_cold_path.exists()
    assert file_to_move.read_text() == "This is content for a file to be moved."


def test_file_lifecycle_symlink_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with SYMLINK operation.

    Phase 1 – Freeze:
        Create file → scan with never-matching criteria
        → file moved to cold, symlink left at hot path.
    Phase 2 – Thaw:
        Change criteria to always-matching → scan
        → symlink detected, cold file moved back to hot, symlink removed.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "symlink"
    db_session.commit()

    # ---- Phase 1: Freeze ----
    file_to_symlink = hot_path / "test_file_symlink.txt"
    file_to_symlink.write_text("This is content for a file to be symlinked.")
    original_mtime = file_to_symlink.stat().st_mtime

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1, f"errors: {results['errors']}"
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify file system: symlink at hot, real file at cold
    assert file_to_symlink.is_symlink()
    expected_cold_path = cold_path / "test_file_symlink.txt"
    assert expected_cold_path.exists()
    assert file_to_symlink.resolve() == expected_cold_path
    assert expected_cold_path.read_text() == "This is content for a file to be symlinked."
    assert expected_cold_path.stat().st_mtime == original_mtime

    # Verify inventory: for SYMLINK, record is updated to the cold destination path
    db_session.expire_all()
    inv = db_session.query(FileInventory).filter_by(
        file_path=str(expected_cold_path)
    ).first()
    assert inv is not None
    assert inv.storage_type == StorageType.COLD
    assert inv.status == FileStatus.ACTIVE

    # ---- Phase 2: Thaw ----
    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    # Hot scan detects symlink-to-cold + is_active=True → thaw
    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_found"] == 0   # nothing to freeze
    assert results_thaw["files_moved"] == 1   # one thaw
    assert results_thaw["errors"] == []

    # Verify file system: symlink gone, real file back at hot path
    assert not file_to_symlink.is_symlink()
    assert file_to_symlink.exists()
    assert not expected_cold_path.exists()
    assert file_to_symlink.read_text() == "This is content for a file to be symlinked."


def test_file_lifecycle_copy_operation(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with COPY operation.

    Phase 1 – Freeze:
        Create file → scan with never-matching criteria
        → cold copy created, hot original preserved.
    Phase 2 – "Thaw":
        Delete cold copy manually, change criteria to always-matching → re-scan.
        Hot file matches → stays in hot (files_found=0, files_moved=0).
        FileRecord for deleted cold copy is cleaned up (files_cleaned > 0).

    Note: The cold FileInventory entry is created on the *next* scan after the copy
    (the inventory update in _scan_path runs before _process_single_file copies the
    file).  We therefore verify it after a second scan, not immediately.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "copy"
    db_session.commit()

    # ---- Phase 1: Freeze ----
    file_to_copy = hot_path / "test_file_copy.txt"
    file_to_copy.write_text("This is content for a file to be copied.")
    original_mtime = file_to_copy.stat().st_mtime

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_found"] == 1, f"errors: {results['errors']}"
    assert results["files_moved"] == 1
    assert results["errors"] == []

    # Verify file system: original still in hot, copy in cold
    assert file_to_copy.exists()
    assert file_to_copy.read_text() == "This is content for a file to be copied."
    expected_cold_path = cold_path / "test_file_copy.txt"
    assert expected_cold_path.exists()
    assert expected_cold_path.read_text() == "This is content for a file to be copied."
    assert expected_cold_path.stat().st_mtime == original_mtime

    # Verify hot inventory: hot file record exists as HOT
    db_session.expire_all()
    hot_inv = db_session.query(FileInventory).filter_by(
        file_path=str(file_to_copy)
    ).first()
    assert hot_inv is not None
    assert hot_inv.storage_type == StorageType.HOT
    assert hot_inv.status == FileStatus.ACTIVE

    # ---- Phase 2: "Thaw" – delete cold copy, re-scan with always-matching criteria ----
    expected_cold_path.unlink()

    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    # Hot file matches criteria → stays hot; nothing new to freeze or move
    assert results_thaw["files_found"] == 0
    assert results_thaw["files_moved"] == 0
    assert results_thaw["errors"] == []
    # Note: cleanup_missing_files keeps the COPY FileRecord as long as the original
    # hot file still exists, so files_cleaned may be 0 here.

    # Verify hot file still exists
    assert file_to_copy.exists()
    assert not expected_cold_path.exists()

    # Verify hot inventory still shows HOT
    db_session.expire_all()
    hot_inv = db_session.query(FileInventory).filter_by(
        file_path=str(file_to_copy)
    ).first()
    assert hot_inv is not None
    assert hot_inv.storage_type == StorageType.HOT
    assert hot_inv.status == FileStatus.ACTIVE


def test_file_lifecycle_non_existent_file(monitored_path_with_locations, db_session):
    """
    Test that the workflow handles a file disappearing between scan and process.

    When _process_single_file detects the file is gone it returns
    {"success": True, "skipped": True}.  process_path counts any success=True result
    as files_moved += 1, so files_moved == 1 even for a skipped file.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"
    db_session.commit()

    file_to_disappear = hot_path / "disappearing_file.txt"
    file_to_disappear.write_text("I will vanish!")

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    with patch(
        "app.services.file_workflow_service.FileWorkflowService._process_single_file"
    ) as mock_process:
        # Simulate file disappearing: success=True means the "processing" completed
        # (gracefully handled the missing file); process_path still counts it as moved.
        mock_process.return_value = {"success": True, "skipped": True}

        results = file_workflow_service.process_path(monitored_path, db_session)

        # _scan_path found the file before it vanished
        assert results["files_found"] == 1
        # success=True → files_moved is incremented (by design; skipped files are
        # treated as successfully handled, not as failures)
        assert results["files_moved"] == 1
        assert results["errors"] == []

    # Clean up the file (it was never actually moved by the mock)
    file_to_disappear.unlink()


# ---------------------------------------------------------------------------
# Operation type migration — end-to-end
# ---------------------------------------------------------------------------

def test_symlink_to_move_migration(monitored_path_with_locations, db_session):
    """
    End-to-end: switch from SYMLINK → MOVE with an already-frozen file.

    Phase 1 – Freeze with SYMLINK:
        File moves to cold, symlink left at hot path.
    Phase 2 – Change operation_type to MOVE, re-scan:
        Orphaned symlink is deleted; cold file remains untouched.
        Nothing is thawed — the file is already where MOVE wants it (cold only).
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "symlink"
    db_session.commit()

    test_file = hot_path / "movie.mkv"
    test_file.write_text("movie content")
    original_content = test_file.read_text()

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    # ---- Phase 1: Freeze with SYMLINK ----
    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_moved"] == 1, f"errors: {results['errors']}"

    expected_cold = cold_path / "movie.mkv"
    assert test_file.is_symlink()
    assert expected_cold.exists()
    assert test_file.resolve() == expected_cold

    # ---- Phase 2: Switch to MOVE, re-scan ----
    monitored_path.operation_type = "move"
    db_session.commit()

    results2 = file_workflow_service.process_path(monitored_path, db_session)
    assert results2["errors"] == []

    # Symlink must be gone; cold file must be intact with original content
    assert not test_file.exists()
    assert expected_cold.exists()
    assert expected_cold.read_text() == original_content

    # Nothing thawed — the file was already correctly in cold storage
    assert results2["files_moved"] == 0


def test_symlink_to_copy_migration(monitored_path_with_locations, db_session):
    """
    End-to-end: switch from SYMLINK → COPY with an already-frozen file.

    Phase 1 – Freeze with SYMLINK:
        File moves to cold, symlink left at hot path.
    Phase 2 – Change operation_type to COPY, scan 1:
        Orphaned symlink is thawed: cold file moves back to hot, symlink removed.
    Phase 3 – Scan 2 (same criteria):
        Hot file is now a real file; COPY freezes it — cold copy created,
        hot original preserved.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "symlink"
    db_session.commit()

    test_file = hot_path / "document.pdf"
    test_file.write_text("document content")
    original_content = test_file.read_text()

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    # ---- Phase 1: Freeze with SYMLINK ----
    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_moved"] == 1, f"errors: {results['errors']}"

    expected_cold = cold_path / "document.pdf"
    assert test_file.is_symlink()
    assert expected_cold.exists()

    # ---- Phase 2: Switch to COPY, scan 1 — thaw ----
    monitored_path.operation_type = "copy"
    db_session.commit()

    results2 = file_workflow_service.process_path(monitored_path, db_session)
    assert results2["errors"] == []

    # Symlink gone, real file restored to hot, cold copy removed during thaw
    assert test_file.exists()
    assert not test_file.is_symlink()
    assert test_file.read_text() == original_content
    assert not expected_cold.exists()

    # ---- Phase 3: Scan 2 — re-freeze as COPY ----
    results3 = file_workflow_service.process_path(monitored_path, db_session)
    assert results3["files_moved"] == 1, f"errors: {results3['errors']}"
    assert results3["errors"] == []

    # Both hot original and cold copy must now exist
    assert test_file.exists()
    assert not test_file.is_symlink()
    assert test_file.read_text() == original_content
    assert expected_cold.exists()
    assert expected_cold.read_text() == original_content


def test_file_lifecycle_encrypted_local_backend(monitored_path_with_locations, db_session):
    """
    Test end-to-end file lifecycle with encryption enabled on a local backend.
    
    1. Freeze a file: It should be encrypted and renamed to ffenc_<id>.ffenc.
    2. Simulate moving the encrypted file on disk (self-healing test).
    3. Re-scan: The database should heal the paths.
    4. Thaw: The file should be thawed, decrypted, and restored with its original name.
    """
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"

    # Enable encryption on the cold storage location
    location = monitored_path.storage_locations[0]
    location.is_encrypted = True
    db_session.commit()

    # ---- Phase 1: Freeze ----
    test_file = hot_path / "important_secrets.txt"
    test_file.write_text("This is highly confidential plaintext data.")
    original_size = test_file.stat().st_size

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    print("DEBUG process_path results:", results)
    print("DEBUG all inventories:", [(i.id, i.file_path, i.storage_type, i.status) for i in db_session.query(FileInventory).all()])
    assert results["files_moved"] == 1, f"errors: {results['errors']}"

    # Get the file inventory to find its ID
    db_session.expire_all()
    inv_entry = db_session.query(FileInventory).filter(
        FileInventory.path_id == monitored_path.id,
        FileInventory.storage_type == StorageType.COLD,
    ).first()
    assert inv_entry is not None
    assert inv_entry.is_encrypted is True

    # Verify file name on disk is ffenc_<id>.ffenc
    expected_cold_name = f"ffenc_{inv_entry.id}.ffenc"
    expected_cold_path = cold_path / expected_cold_name
    assert expected_cold_path.exists()
    assert inv_entry.file_path == str(expected_cold_path)

    # The file contents should be encrypted (not readable as plaintext)
    encrypted_contents = expected_cold_path.read_bytes()
    assert b"confidential" not in encrypted_contents

    # ---- Phase 2: Self-Healing / Moving on disk ----
    # Manually move the file to a subfolder on disk (simulating movement/renaming)
    subfolder = cold_path / "archive"
    subfolder.mkdir(exist_ok=True)
    new_cold_path = subfolder / expected_cold_name
    expected_cold_path.rename(new_cold_path)

    assert not expected_cold_path.exists()
    assert new_cold_path.exists()

    # Run scan. The scanner should detect the file at the new path, match the ID from the filename,
    # and update the DB paths.
    results_heal = file_workflow_service.process_path(monitored_path, db_session)
    assert results_heal["errors"] == []

    # Verify DB references have been healed
    db_session.expire_all()
    inv_healed = db_session.query(FileInventory).filter(FileInventory.id == inv_entry.id).first()
    assert inv_healed.file_path == str(new_cold_path)
    assert inv_healed.status == FileStatus.ACTIVE

    rec_healed = db_session.query(FileRecord).filter(
        FileRecord.path_id == monitored_path.id,
        FileRecord.cold_storage_path == str(new_cold_path),
    ).first()
    assert rec_healed is not None
    assert rec_healed.original_path == str(test_file)

    # ---- Phase 3: Thaw & Decrypt ----
    # Swap criteria to trigger thaw
    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_moved"] == 1
    assert results_thaw["errors"] == []

    # Verify that the decrypted file is back in hot storage with original name and content
    assert test_file.exists()
    assert test_file.read_text() == "This is highly confidential plaintext data."
    assert not new_cold_path.exists()


def test_file_lifecycle_encrypted_gdrive_backend(monitored_path_with_locations, db_session, monkeypatch):
    """
    Test end-to-end file lifecycle with encryption enabled on a Google Drive backend.
    
    1. Freeze a file: It should be encrypted and uploaded to GDrive as ffenc_<id>.ffenc.
    2. Simulate a file ID change on Google Drive (self-healing test).
    3. Re-scan: Google Drive listing is scanned, and the DB heals references to the new file ID.
    4. Thaw: The file should be thawed, decrypted, and restored in hot storage with original name and content.
    """
    from app.models import ColdStorageBackendType, OperationType
    monitored_path, hot_path, cold_path = monitored_path_with_locations
    monitored_path.operation_type = "move"

    # Change the storage location to be GDrive
    location = monitored_path.storage_locations[0]
    location.backend_type = ColdStorageBackendType.GDRIVE
    location.path = "gdrive://test-folder"
    location.is_encrypted = True
    db_session.commit()

    files_dict = {}

    class MockGDriveBackend:
        def backend_name(self) -> str:
            return "gdrive"

        def validate_location(self, location):
            return True, None

        def capabilities(self):
            from app.services.cold_storage_backends.base import ColdStorageCapabilities
            return ColdStorageCapabilities(
                supports_move=True,
                supports_copy=True,
                supports_symlink=False,
                supports_local_path_stats=False,
            )

        def build_reference(self, location, path) -> str:
            return f"gdrive://{location.id}/{path}"

        def freeze_file(self, source_path, relative_path, location, operation_mode, progress_callback=None):
            file_id = f"file-{relative_path.name}"
            ref = self.build_reference(location, file_id)
            files_dict[ref] = {
                "content": source_path.read_bytes(),
                "name": relative_path.name,
                "id": file_id,
            }
            if operation_mode == OperationType.MOVE:
                source_path.unlink()
            return True, None, ref, None

        def thaw_file(self, storage_reference, destination_path, location, operation_mode):
            if storage_reference not in files_dict:
                return False, f"File not found: {storage_reference}"
            destination_path.write_bytes(files_dict[storage_reference]["content"])
            if operation_mode in (OperationType.MOVE, OperationType.SYMLINK):
                del files_dict[storage_reference]
            return True, None

        def exists(self, storage_reference, location) -> bool:
            return storage_reference in files_dict

        def list_all_folder_files(self, location, page_size=1000, page_token=None):
            files = []
            for ref, info in files_dict.items():
                if ref.startswith(f"gdrive://{location.id}/"):
                    files.append({
                        "id": info["id"],
                        "name": info["name"],
                        "size": len(info["content"]),
                        "modified_time": "2026-06-01T15:00:00Z",
                        "created_time": "2026-06-01T15:00:00Z",
                        "is_managed": False,
                        "ff_location_id": None,
                    })
            return {"files": files}

        def list_managed_files(self, location, page_size=1000, page_token=None):
            return self.list_all_folder_files(location, page_size, page_token)

    mock_backend = MockGDriveBackend()

    # Patch get_backend
    monkeypatch.setattr("app.services.file_workflow_service.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_freezer.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_thawer.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_cleanup.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.storage_routing_service.get_backend", lambda loc: mock_backend)

    # ---- Phase 1: Freeze ----
    test_file = hot_path / "gdrive_secrets.txt"
    test_file.write_text("Top secret GDrive plaintext.")
    original_size = test_file.stat().st_size

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_moved"] == 1, f"errors: {results['errors']}"

    db_session.expire_all()
    inv_entry = db_session.query(FileInventory).filter(
        FileInventory.path_id == monitored_path.id,
        FileInventory.storage_type == StorageType.COLD,
    ).first()
    assert inv_entry is not None
    assert inv_entry.is_encrypted is True

    expected_cold_name = f"ffenc_{inv_entry.id}.ffenc"
    expected_file_id = f"file-{expected_cold_name}"
    expected_ref = f"gdrive://{location.id}/{expected_file_id}"
    assert inv_entry.file_path == expected_ref

    assert expected_ref in files_dict
    assert b"secrets" not in files_dict[expected_ref]["content"]

    # ---- Phase 2: Self-Healing / File ID change on remote ----
    # Simulate the file being re-uploaded to GDrive, changing its file ID on the remote
    new_file_id = "file-id-changed-999"
    new_ref = f"gdrive://{location.id}/{new_file_id}"

    # Update our mock storage with the new file reference
    file_info = files_dict.pop(expected_ref)
    file_info["id"] = new_file_id
    files_dict[new_ref] = file_info

    # Run scan to trigger self-healing
    results_heal = file_workflow_service.process_path(monitored_path, db_session)
    assert results_heal["errors"] == []

    # Verify DB references have been healed to the new ref
    db_session.expire_all()
    inv_healed = db_session.query(FileInventory).filter(FileInventory.id == inv_entry.id).first()
    assert inv_healed.file_path == new_ref
    assert inv_healed.status == FileStatus.ACTIVE

    rec_healed = db_session.query(FileRecord).filter(
        FileRecord.path_id == monitored_path.id,
        FileRecord.cold_storage_path == new_ref,
    ).first()
    assert rec_healed is not None

    # ---- Phase 3: Thaw & Decrypt ----
    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_moved"] == 1
    assert results_thaw["errors"] == []

    # Verify that the decrypted file is back in hot storage
    assert test_file.exists()
    assert test_file.read_text() == "Top secret GDrive plaintext."
    assert new_ref not in files_dict


def test_file_lifecycle_encrypted_s3_backend(monitored_path_with_locations, db_session, monkeypatch):
    """
    Test end-to-end file lifecycle with encryption enabled on an S3 backend.
    
    1. Freeze a file: It should be encrypted and uploaded to S3 as ffenc_<id>.ffenc.
    2. Thaw: The file should be thawed, decrypted, and restored in hot storage with original name and content.
    """
    from app.models import ColdStorageBackendType, OperationType
    monitored_path, hot_path, _ = monitored_path_with_locations
    monitored_path.operation_type = "move"

    # Change the storage location to be S3
    location = monitored_path.storage_locations[0]
    location.backend_type = ColdStorageBackendType.S3
    location.path = "s3://my-bucket/prefix"
    location.is_encrypted = True
    db_session.commit()

    files_dict = {}

    class MockS3Backend:
        def backend_name(self) -> str:
            return "s3"

        def validate_location(self, location):
            return True, None

        def capabilities(self):
            from app.services.cold_storage_backends.base import ColdStorageCapabilities
            return ColdStorageCapabilities(
                supports_move=True,
                supports_copy=True,
                supports_symlink=False,
                supports_local_path_stats=False,
            )

        def build_reference(self, location, path) -> str:
            return f"s3://my-bucket/prefix/{path}"

        def freeze_file(self, source_path, relative_path, location, operation_mode, progress_callback=None):
            ref = self.build_reference(location, relative_path)
            files_dict[ref] = {
                "content": source_path.read_bytes(),
            }
            if operation_mode == OperationType.MOVE:
                source_path.unlink()
            return True, None, ref, None

        def thaw_file(self, storage_reference, destination_path, location, operation_mode):
            if storage_reference not in files_dict:
                return False, f"File not found: {storage_reference}"
            destination_path.write_bytes(files_dict[storage_reference]["content"])
            if operation_mode in (OperationType.MOVE, OperationType.SYMLINK):
                del files_dict[storage_reference]
            return True, None

        def exists(self, storage_reference, location) -> bool:
            return storage_reference in files_dict

    mock_backend = MockS3Backend()

    # Patch get_backend
    monkeypatch.setattr("app.services.file_workflow_service.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_freezer.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_thawer.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.file_cleanup.get_backend", lambda loc: mock_backend)
    monkeypatch.setattr("app.services.storage_routing_service.get_backend", lambda loc: mock_backend)

    # ---- Phase 1: Freeze ----
    test_file = hot_path / "s3_secrets.txt"
    test_file.write_text("Top secret S3 plaintext.")

    criteria = _never_match_criterion(monitored_path.id)
    db_session.add(criteria)
    db_session.commit()

    results = file_workflow_service.process_path(monitored_path, db_session)
    assert results["files_moved"] == 1, f"errors: {results['errors']}"

    db_session.expire_all()
    inv_entry = db_session.query(FileInventory).filter(
        FileInventory.path_id == monitored_path.id,
        FileInventory.storage_type == StorageType.COLD,
    ).first()
    assert inv_entry is not None
    assert inv_entry.is_encrypted is True

    expected_cold_name = f"ffenc_{inv_entry.id}.ffenc"
    expected_ref = f"s3://my-bucket/prefix/{expected_cold_name}"
    assert inv_entry.file_path == expected_ref

    assert expected_ref in files_dict
    assert b"secrets" not in files_dict[expected_ref]["content"]

    # ---- Phase 2: Thaw & Decrypt ----
    criteria.operator = ">"
    criteria.value = "-1"
    db_session.commit()

    results_thaw = file_workflow_service.process_path(monitored_path, db_session)
    assert results_thaw["files_moved"] == 1
    assert results_thaw["errors"] == []

    # Verify that the decrypted file is back in hot storage
    assert test_file.exists()
    assert test_file.read_text() == "Top secret S3 plaintext."
    assert expected_ref not in files_dict
