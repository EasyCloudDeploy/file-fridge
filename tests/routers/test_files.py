import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    MonitoredPath,
    PinnedFile,
    StorageType,
    Tag,
)
from app.schemas import StorageType as StorageTypeSchema


@pytest.fixture
def file_inventory_factory(db_session: Session, monitored_path_factory, storage_location):
    """Factory for creating FileInventory entries."""

    def _factory(
        file_path: str,
        path_name: str = "test_path",
        storage_type: StorageType = StorageType.HOT,
        status: FileStatus = FileStatus.ACTIVE,
        file_size: int = 1024,
        file_mtime: datetime = datetime.now(timezone.utc),
        file_atime: datetime = datetime.now(timezone.utc),
        file_ctime: datetime = datetime.now(timezone.utc),
        checksum: str = None,
        file_extension: str = ".txt",
        mime_type: str = "text/plain",
        is_pinned: bool = False,
        cold_storage_location: ColdStorageLocation = None,
        monitored_path: MonitoredPath = None,
    ):
        if monitored_path is None:
            monitored_path = monitored_path_factory(path_name, str(Path(file_path).parent))

        if cold_storage_location is None and storage_type == StorageType.COLD:
            cold_storage_location = storage_location

        inventory_entry = FileInventory(
            path_id=monitored_path.id,
            file_path=file_path,
            storage_type=storage_type,
            file_size=file_size,
            file_mtime=file_mtime,
            file_atime=file_atime,
            file_ctime=file_ctime,
            checksum=checksum,
            file_extension=file_extension,
            mime_type=mime_type,
            status=status,
            last_seen=datetime.now(timezone.utc),
            cold_storage_location_id=cold_storage_location.id if cold_storage_location else None,
        )
        db_session.add(inventory_entry)
        db_session.commit()
        db_session.refresh(inventory_entry)

        if is_pinned:
            pinned_file = PinnedFile(path_id=monitored_path.id, file_path=file_path)
            db_session.add(pinned_file)
            db_session.commit()
            db_session.refresh(pinned_file)

        return inventory_entry

    return _factory


@pytest.fixture
def create_tag(db_session: Session):
    """Fixture to create a Tag."""

    def _factory(name: str, color: str = "#FFFFFF"):
        tag = Tag(name=name, color=color)
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)
        return tag

    return _factory


def test_list_files_no_filters(authenticated_client: TestClient, file_inventory_factory, tmp_path):
    """Test basic listing of files without any filters."""
    file_inventory_factory(str(tmp_path / "file1.txt"), path_name="path1")
    file_inventory_factory(str(tmp_path / "file2.jpg"), path_name="path2")

    response = authenticated_client.get("/api/v1/files")
    assert response.status_code == 200

    lines = response.content.decode().strip().split("\n")
    metadata = json.loads(lines[0])
    files = [json.loads(line)["data"] for line in lines[1:-1]]
    completion = json.loads(lines[-1])

    assert metadata["type"] == "metadata"
    assert metadata["total"] == 2
    assert len(files) == 2
    assert completion["type"] == "complete"
    assert completion["count"] == 2


@pytest.mark.parametrize(
    "setup_kwargs, filter_query, expected_path_part, expected_attr, expected_value",
    [
        # path_id filter (special case: dynamic value)
        (
            {"path_name": "path1_data"},
            "path_id={path_id}",
            "file.txt",
            "file_path",
            None,  # Value checked dynamically
        ),
        # storage_type filter
        (
            {"storage_type": StorageType.COLD},
            f"storage_type={StorageTypeSchema.COLD.value}",
            "cold_file.txt",
            "storage_type",
            StorageTypeSchema.COLD.value,
        ),
        # status filter
        (
            {"status": FileStatus.MIGRATING},
            f"status={FileStatus.MIGRATING.value}",
            "migrating.txt",
            "status",
            FileStatus.MIGRATING.value,
        ),
        # search filter
        (
            {},
            "search=doc",
            "document.pdf",
            "file_path",
            None,  # Checked by "in"
        ),
        # extension filter
        (
            {"file_extension": ".jpg"},
            "extension=.jpg",
            "file2.jpg",
            "file_extension",
            ".jpg",
        ),
        # mime_type filter
        (
            {"mime_type": "image/jpeg"},
            "mime_type=image",
            "file2.jpg",
            "mime_type",
            "image/jpeg",
        ),
        # has_checksum=true
        (
            {"checksum": "abc"},
            "has_checksum=true",
            "file1.txt",
            "checksum",
            "abc",
        ),
        # has_checksum=false
        (
            {"checksum": None},
            "has_checksum=false",
            "file2.txt",
            "checksum",
            None,
        ),
    ],
)
def test_list_files_filters(
    authenticated_client: TestClient,
    file_inventory_factory,
    tmp_path,
    setup_kwargs,
    filter_query,
    expected_path_part,
    expected_attr,
    expected_value,
):
    """Test various file filters using parameterization to reduce duplication."""

    # Create test files based on the scenario
    if "path_id" in filter_query:
        file1 = file_inventory_factory(
            str(tmp_path / "path1" / "file.txt"), path_name="path1_data"
        )
        file_inventory_factory(str(tmp_path / "path2" / "file.txt"), path_name="path2_data")
        query = filter_query.format(path_id=file1.path_id)
        expected_val = str(file1.file_path)
    elif "storage_type" in filter_query:
        file_inventory_factory(str(tmp_path / "hot_file.txt"), storage_type=StorageType.HOT)
        file_inventory_factory(str(tmp_path / "cold_file.txt"), storage_type=StorageType.COLD)
        query = filter_query
        expected_val = expected_value
    elif "status" in filter_query:
        file_inventory_factory(str(tmp_path / "active.txt"), status=FileStatus.ACTIVE)
        file_inventory_factory(str(tmp_path / "migrating.txt"), status=FileStatus.MIGRATING)
        query = filter_query
        expected_val = expected_value
    elif "search" in filter_query:
        file_inventory_factory(str(tmp_path / "document.pdf"))
        file_inventory_factory(str(tmp_path / "image.jpg"))
        query = filter_query
        expected_val = None # Checked manually
    elif "extension" in filter_query:
        file_inventory_factory(str(tmp_path / "file1.txt"), file_extension=".txt")
        file_inventory_factory(str(tmp_path / "file2.jpg"), file_extension=".jpg")
        query = filter_query
        expected_val = expected_value
    elif "mime_type" in filter_query:
        file_inventory_factory(str(tmp_path / "file1.txt"), mime_type="text/plain")
        file_inventory_factory(str(tmp_path / "file2.jpg"), mime_type="image/jpeg")
        query = filter_query
        expected_val = expected_value
    elif "has_checksum" in filter_query:
        file_inventory_factory(str(tmp_path / "file1.txt"), checksum="abc")
        file_inventory_factory(str(tmp_path / "file2.txt"), checksum=None)
        query = filter_query
        expected_val = expected_value
    else:
        pytest.fail("Unknown filter scenario")

    response = authenticated_client.get(f"/api/v1/files?{query}")
    assert response.status_code == 200

    lines = response.content.decode().strip().split("\n")
    metadata = json.loads(lines[0])
    files = [json.loads(line)["data"] for line in lines[1:-1]]

    assert metadata["total"] == 1
    assert len(files) == 1

    file_data = files[0]

    # Check if correct file returned (by path)
    if expected_path_part:
        assert expected_path_part in file_data["file_path"]

    # Check expected attribute value
    if expected_value is not None:
        assert file_data[expected_attr] == expected_value
    elif expected_attr == "file_path" and "search" in filter_query:
        assert "document" in file_data["file_path"]
    elif expected_attr == "file_path" and "path_id" in filter_query:
        assert file_data["file_path"] == expected_val


@patch("app.services.file_mover.FileMover.move_file")
def test_move_file_success(mock_move_file, authenticated_client: TestClient, tmp_path):
    """Test successful on-demand file move."""
    source_path = tmp_path / "source.txt"
    source_path.write_text("test content")
    destination_path = tmp_path / "dest.txt"

    mock_move_file.return_value = (True, None)

    response = authenticated_client.post(
        "/api/v1/files/move",
        json={
            "source_path": str(source_path),
            "destination_path": str(destination_path),
            "operation_type": "move",
        },
    )
    print(response.json()); assert response.status_code == 202
    assert response.json()["message"] == "File moved successfully"
    mock_move_file.assert_called_once()


@patch("app.services.file_thawer.FileThawer.thaw_file")
def test_thaw_file_success(
    mock_thaw_file,
    authenticated_client: TestClient,
    file_inventory_factory,
    tmp_path,
    db_session: Session,
):
    """Test successful thaw of a file."""
    cold_file = file_inventory_factory(
        str(tmp_path / "cold_file.txt"), storage_type=StorageType.COLD
    )

    # We need a corresponding FileRecord for FileThawer to work
    file_record = FileRecord(
        original_path=str(tmp_path / "hot_location" / "cold_file.txt"),
        cold_storage_path=str(cold_file.file_path),
        file_size=cold_file.file_size,
        operation_type="move",
    )
    db_session.add(file_record)
    db_session.commit()

    mock_thaw_file.return_value = (True, None)

    response = authenticated_client.post(f"/api/v1/files/thaw/{cold_file.id}")
    assert response.status_code == 200
    assert response.json()["message"] == "File thawed successfully"
    mock_thaw_file.assert_called_once()


@patch("app.services.file_freezer.FileFreezer.freeze_file")
def test_freeze_file_success(
    mock_freeze_file,
    authenticated_client: TestClient,
    file_inventory_factory,
    storage_location,
    tmp_path,
):
    """Test successful freeze of a file."""
    hot_file = file_inventory_factory(str(tmp_path / "hot_file.txt"), storage_type=StorageType.HOT)

    mock_freeze_file.return_value = (True, None, "/tmp/cold_path/hot_file.txt")

    response = authenticated_client.post(
        f"/api/v1/files/freeze/{hot_file.id}?storage_location_id={storage_location.id}"
    )
    assert response.status_code == 200
    assert "File frozen successfully" in response.json()["message"]
    mock_freeze_file.assert_called_once()


def test_get_freeze_options(
    authenticated_client: TestClient, file_inventory_factory, storage_location, tmp_path
):
    """Test retrieving freeze options for a file."""
    hot_file = file_inventory_factory(str(tmp_path / "hot_file.txt"), storage_type=StorageType.HOT)

    response = authenticated_client.get(f"/api/v1/files/freeze/{hot_file.id}/options")
    assert response.status_code == 200
    data = response.json()
    assert data["inventory_id"] == hot_file.id
    assert data["can_freeze"] is True
    assert len(data["available_locations"]) == 1
    assert data["available_locations"][0]["id"] == storage_location.id


@patch("app.services.relocation_manager.relocation_manager.create_task")
def test_relocate_file_success(
    mock_create_task,
    authenticated_client: TestClient,
    file_inventory_factory,
    storage_location,
    monitored_path_factory,
    tmp_path,
    db_session: Session,
):
    """Test successful relocation of a file."""
    cold_loc1 = storage_location  # Use the default fixture
    cold_loc2 = ColdStorageLocation(
        name="Cold Loc 2", path=str(tmp_path / "cold2")
    )
    db_session.add(cold_loc2)
    db_session.commit()
    Path(cold_loc2.path).mkdir(exist_ok=True, parents=True)

    monitored_path = monitored_path_factory(
        "RelocatePath",
        str(tmp_path / "relocate_hot"),
        storage_locations=[cold_loc1, cold_loc2]
    )

    cold_file = file_inventory_factory(
        str(Path(cold_loc1.path) / "relocate_file.txt"),
        storage_type=StorageType.COLD,
        cold_storage_location=cold_loc1,
        monitored_path=monitored_path,
    )
    # Create the file on disk so the endpoint finds it
    Path(cold_file.file_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cold_file.file_path).touch()

    mock_create_task.return_value = "relocation_task_id_123"

    response = authenticated_client.post(
        f"/api/v1/files/relocate/{cold_file.id}",
        json={"target_storage_location_id": cold_loc2.id},
    )
    print(response.json()); assert response.status_code == 202
    assert response.json()["task_id"] == "relocation_task_id_123"
    mock_create_task.assert_called_once()


@patch("app.services.metadata_backfill.MetadataBackfillService.backfill_all")
@patch("app.services.metadata_backfill.MetadataBackfillService.__init__", return_value=None)
def test_metadata_backfill(mock_init, mock_backfill_all, authenticated_client: TestClient):
    """Test triggering metadata backfill."""
    mock_backfill_all.return_value = {"processed": 10, "updated": 5, "errors": 0}

    response = authenticated_client.post("/api/v1/files/metadata/backfill")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["processed"] == 10
    mock_init.assert_called_once()
    mock_backfill_all.assert_called_once_with(batch_size=100, compute_checksum=True)


def test_pin_file_success(
    authenticated_client: TestClient, file_inventory_factory, tmp_path, db_session: Session
):
    """Test pinning a file."""
    file_to_pin = file_inventory_factory(str(tmp_path / "pin_me.txt"))

    response = authenticated_client.post(f"/api/v1/files/{file_to_pin.id}/pin")
    assert response.status_code == 200
    assert response.json()["is_pinned"] is True

    # Verify in DB
    pinned = (
        db_session.query(PinnedFile).filter(PinnedFile.file_path == file_to_pin.file_path).first()
    )
    assert pinned is not None


def test_unpin_file_success(
    authenticated_client: TestClient, file_inventory_factory, tmp_path, db_session: Session
):
    """Test unpinning a file."""
    file_to_unpin = file_inventory_factory(str(tmp_path / "unpin_me.txt"), is_pinned=True)

    response = authenticated_client.delete(f"/api/v1/files/{file_to_unpin.id}/pin")
    assert response.status_code == 200
    assert response.json()["is_pinned"] is False

    # Verify in DB
    pinned = (
        db_session.query(PinnedFile)
        .filter(PinnedFile.file_path == file_to_unpin.file_path)
        .first()
    )
    assert pinned is None


@patch("app.services.file_thawer.FileThawer.thaw_file", return_value=(True, None))
def test_bulk_thaw_files(
    mock_thaw_file,
    authenticated_client: TestClient,
    file_inventory_factory,
    tmp_path,
    db_session: Session,
):
    """Test bulk thawing of files."""
    file1 = file_inventory_factory(str(tmp_path / "bulk_cold_1.txt"), storage_type=StorageType.COLD)
    file2 = file_inventory_factory(str(tmp_path / "bulk_cold_2.txt"), storage_type=StorageType.COLD)

    # Create file records
    for f in [file1, file2]:
        record = FileRecord(
            original_path=f"/hot/{f.file_path}",
            cold_storage_path=f.file_path,
            file_size=f.file_size,
            operation_type="move",
        )
        db_session.add(record)
    db_session.commit()

    response = authenticated_client.post(
        "/api/v1/files/bulk/thaw",
        json={"file_ids": [file1.id, file2.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 2
    assert data["failed"] == 0
    assert mock_thaw_file.call_count == 2


@patch("app.services.file_freezer.FileFreezer.freeze_file", return_value=(True, None, "/cold/path"))
def test_bulk_freeze_files(
    mock_freeze_file,
    authenticated_client: TestClient,
    file_inventory_factory,
    storage_location,
    tmp_path,
):
    """Test bulk freezing of files."""
    file1 = file_inventory_factory(str(tmp_path / "bulk_hot_1.txt"), storage_type=StorageType.HOT)
    file2 = file_inventory_factory(str(tmp_path / "bulk_hot_2.txt"), storage_type=StorageType.HOT)

    response = authenticated_client.post(
        "/api/v1/files/bulk/freeze",
        json={"file_ids": [file1.id, file2.id], "storage_location_id": storage_location.id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 2
    assert data["failed"] == 0
    assert mock_freeze_file.call_count == 2


def test_bulk_pin_files(
    authenticated_client: TestClient, file_inventory_factory, tmp_path, db_session: Session
):
    """Test bulk pinning of files."""
    file1 = file_inventory_factory(str(tmp_path / "bulk_pin_1.txt"))
    file2 = file_inventory_factory(str(tmp_path / "bulk_pin_2.txt"))

    response = authenticated_client.post(
        "/api/v1/files/bulk/pin",
        json={"file_ids": [file1.id, file2.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 2
    assert data["failed"] == 0

    # Verify in DB
    pinned_count = (
        db_session.query(PinnedFile)
        .filter(PinnedFile.file_path.in_([str(file1.file_path), str(file2.file_path)]))
        .count()
    )
    assert pinned_count == 2


def test_bulk_unpin_files(
    authenticated_client: TestClient, file_inventory_factory, tmp_path, db_session: Session
):
    """Test bulk unpinning of files."""
    file1 = file_inventory_factory(str(tmp_path / "bulk_unpin_1.txt"), is_pinned=True)
    file2 = file_inventory_factory(str(tmp_path / "bulk_unpin_2.txt"), is_pinned=True)

    response = authenticated_client.post(
        "/api/v1/files/bulk/unpin",
        json={"file_ids": [file1.id, file2.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 2
    assert data["failed"] == 0

    # Verify in DB
    pinned_count = (
        db_session.query(PinnedFile)
        .filter(PinnedFile.file_path.in_([str(file1.file_path), str(file2.file_path)]))
        .count()
    )
    assert pinned_count == 0
