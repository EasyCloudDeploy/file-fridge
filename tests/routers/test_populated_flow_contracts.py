import json
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    FileInventory,
    FileRecord,
    FileStatus,
    FileTag,
    Notifier,
    NotifierType,
    OperationType,
    TagRule,
    StorageType,
)


def _parse_file_stream(response):
    lines = response.content.decode().strip().split("\n")
    metadata = json.loads(lines[0])
    files = [json.loads(line)["data"] for line in lines[1:-1]]
    completion = json.loads(lines[-1])
    return metadata, files, completion


def test_populated_flow_endpoints_return_consistent_data(
    authenticated_client,
    db_session,
    monitored_path_factory,
    storage_location,
    create_tag,
    monkeypatch,
    tmp_path,
):
    hot_path = tmp_path / "ff-test-populated-hot"
    hot_path.mkdir(parents=True, exist_ok=True)

    monitored_path = monitored_path_factory("ff-test-populated-path", str(hot_path))
    db_session.refresh(monitored_path)
    db_session.refresh(storage_location)
    monitored_path_id = monitored_path.id
    monitored_path_name = monitored_path.name
    storage_location_id = storage_location.id
    storage_location_path = storage_location.path

    source_file = hot_path / "ff-test-file.txt"
    source_file.write_text("file fridge populated flow test")

    inventory = FileInventory(
        path_id=monitored_path_id,
        file_path=str(source_file),
        file_size=source_file.stat().st_size,
        file_mtime=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        status=FileStatus.ACTIVE,
        storage_type=StorageType.HOT,
    )
    db_session.add(inventory)
    db_session.commit()
    db_session.refresh(inventory)
    inventory_id = inventory.id
    inventory_file_path = inventory.file_path
    inventory_storage_type = inventory.storage_type.value

    tag = create_tag("ff-test-tag-populated", color="#2255aa")
    tag_id = tag.id
    db_session.add(FileTag(file_id=inventory.id, tag_id=tag.id))
    db_session.add(
        TagRule(
            tag_id=tag.id,
            criterion_type="extension",
            operator="=",
            value="txt",
            enabled=True,
            priority=10,
        )
    )
    db_session.add(
        Notifier(
            name="ff-test-notifier-populated",
            type=NotifierType.EMAIL,
            address="ff-test@example.com",
            smtp_host="smtp.example.com",
            smtp_sender="ff-test@example.com",
            enabled=True,
            subscribed_events=["SCAN_COMPLETED"],
        )
    )
    db_session.add(
        FileRecord(
            path_id=monitored_path_id,
            original_path=str(source_file),
            cold_storage_path=str(Path(storage_location_path) / source_file.name),
            file_size=inventory.file_size,
            operation_type=OperationType.MOVE,
            moved_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    from app.services.scheduler import scheduler_service

    triggered_scan_path_ids = []
    monkeypatch.setattr(
        scheduler_service,
        "trigger_scan",
        lambda path_id: triggered_scan_path_ids.append(path_id),
    )

    auth_check_response = authenticated_client.get("/api/v1/auth/check")
    assert auth_check_response.status_code == 200
    assert auth_check_response.json()["setup_required"] is False

    paths_response = authenticated_client.get("/api/v1/paths")
    assert paths_response.status_code == 200
    paths = paths_response.json()
    path_summary = next(item for item in paths if item["id"] == monitored_path_id)
    assert path_summary["name"] == "ff-test-populated-path"
    assert path_summary["file_count"] >= 1
    assert path_summary["hot_file_count"] >= 1

    path_detail_response = authenticated_client.get(f"/api/v1/paths/{monitored_path_id}")
    assert path_detail_response.status_code == 200
    assert path_detail_response.json()["id"] == monitored_path_id

    scan_trigger_response = authenticated_client.post(f"/api/v1/paths/{monitored_path_id}/scan")
    assert scan_trigger_response.status_code == 202
    assert triggered_scan_path_ids == [monitored_path_id]

    storage_locations_response = authenticated_client.get("/api/v1/storage/locations")
    assert storage_locations_response.status_code == 200
    storage_locations = storage_locations_response.json()
    assert any(location["id"] == storage_location_id for location in storage_locations)

    storage_stats_response = authenticated_client.get("/api/v1/storage/stats")
    assert storage_stats_response.status_code == 200
    assert any(item["path"] == storage_location_path for item in storage_stats_response.json())

    files_response = authenticated_client.get("/api/v1/files")
    assert files_response.status_code == 200
    metadata, files, completion = _parse_file_stream(files_response)
    assert metadata["type"] == "metadata"
    assert completion["type"] == "complete"
    file_row = next(item for item in files if item["id"] == inventory_id)
    assert file_row["file_path"] == inventory_file_path
    assert file_row["storage_type"] == inventory_storage_type

    file_tags_response = authenticated_client.get(f"/api/v1/tags/files/{inventory_id}/tags")
    assert file_tags_response.status_code == 200
    assert any(item["tag"]["id"] == tag_id for item in file_tags_response.json())

    tags_response = authenticated_client.get("/api/v1/tags")
    assert tags_response.status_code == 200
    populated_tag = next(item for item in tags_response.json() if item["id"] == tag_id)
    assert populated_tag["file_count"] >= 1

    tag_rules_response = authenticated_client.get("/api/v1/tag-rules")
    assert tag_rules_response.status_code == 200
    assert any(rule["tag_id"] == tag_id for rule in tag_rules_response.json())

    notifiers_response = authenticated_client.get("/api/v1/notifiers")
    assert notifiers_response.status_code == 200
    assert any(item["name"] == "ff-test-notifier-populated" for item in notifiers_response.json())

    stats_response = authenticated_client.get("/api/v1/stats")
    assert stats_response.status_code == 200
    stats_data = stats_response.json()
    assert stats_data["total_files_hot"] >= 1
    assert stats_data["total_files_moved"] >= 1

    detailed_stats_response = authenticated_client.get("/api/v1/stats/detailed")
    assert detailed_stats_response.status_code == 200
    detailed_stats = detailed_stats_response.json()
    assert detailed_stats["total_files_hot"] >= 1
    assert any(item["path_name"] == monitored_path_name for item in detailed_stats["top_paths_by_files"])
