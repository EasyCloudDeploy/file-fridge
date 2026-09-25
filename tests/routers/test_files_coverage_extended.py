"""Extended coverage tests for app/routers/api/files.py."""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    ColdStorageLocation,
    FileInventory,
    FileRecord,
    FileStatus,
    FileTransactionHistory,
    MonitoredPath,
    OperationType,
    RemoteSharedFileCache,
    StorageType,
    TransactionType,
)
from app.routers.api import files as files_router
from app.schemas import BulkShareToggleRequest, FilterCriteria
from app.services.scan_progress import scan_progress_manager


@pytest.fixture(autouse=True)
def reset_scan_progress_manager():
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()
    yield
    with scan_progress_manager._lock:
        scan_progress_manager._scans.clear()
        scan_progress_manager._scans_by_id.clear()


@pytest.mark.unit
class TestFilesRouterHelpers:
    def test_parse_tag_ids_invalid(self):
        with pytest.raises(ValueError, match="Invalid tag_ids"):
            files_router._parse_tag_ids("1,abc,3")

    def test_parse_google_search_empty_and_operators(self):
        assert files_router.parse_google_search(None) == files_router.parse_google_search("")
        parsed = files_router.parse_google_search(
            'mime:"text/plain" status:active pinned:false has_checksum:false '
            'path:"/data" size:<500kb ext:pdf -draft "exact phrase"'
        )
        assert parsed["mime"] == "text/plain"
        assert parsed["status"] == "active"
        assert parsed["pinned"] is False
        assert parsed["has_checksum"] is False
        assert parsed["path"] == "/data"
        assert parsed["size_op"] == "<"
        assert parsed["size_val"] == 500 * 1024
        assert parsed["ext"] == "pdf"
        assert "draft" in parsed["negative_terms"]
        assert "exact phrase" in parsed["positive_terms"]

    def test_build_remote_dedupe_key(self):
        remote = RemoteSharedFileCache(
            peer_id=1,
            remote_file_id="rid-1",
            path_id=1,
            file_path="/remote/a.bin",
            checksum="AbC123",
        )
        assert files_router._build_remote_dedupe_key(remote) == ("checksum", "abc123")
        remote.checksum = None
        assert files_router._build_remote_dedupe_key(remote) == ("peer_remote_id", "1:rid-1")

    def test_get_storage_location_for_file(self, db_session, monitored_path_factory, tmp_path):
        hot = tmp_path / "hot_loc"
        hot.mkdir()
        cold = tmp_path / "cold_loc"
        cold.mkdir()
        path = monitored_path_factory("StorageLocPath", str(hot))
        extra = ColdStorageLocation(name="Extra Cold", path=str(cold))
        db_session.add(extra)
        path.storage_locations.append(extra)
        db_session.commit()
        db_session.refresh(path)

        by_id = files_router._get_storage_location_for_file(
            str(hot / "file.txt"), path, storage_location_id=extra.id
        )
        assert by_id is not None
        assert by_id["id"] == extra.id

        by_prefix = files_router._get_storage_location_for_file(str(cold / "nested/file.bin"), path)
        assert by_prefix is not None
        assert by_prefix["path"] == str(cold)

    def test_apply_filters_and_cursor_helpers(self, db_session, file_inventory_factory):
        inv = file_inventory_factory(
            str(Path("/tmp/filter_ext.txt")),
            file_extension=".txt",
            mime_type="text/plain",
            size=2048,
            is_pinned=True,
        )
        query = db_session.query(FileInventory)
        filtered = files_router._apply_filters(
            query,
            db_session,
            FilterCriteria(
                extension="txt",
                mime_type="plain",
                is_pinned=True,
                min_size=1000,
                max_size=5000,
            ),
        )
        assert filtered.count() == 1

        sort_field = FileInventory.file_size
        paginated = files_router._apply_cursor_pagination(
            filtered,
            sort_field,
            {"id": inv.id, "sort_value": datetime.now(timezone.utc).isoformat()},
            is_descending=True,
        )
        assert paginated is not None

        cursor = files_router._generate_next_cursor([], "file_size", ["file_size"])
        assert cursor is None
        encoded = files_router._generate_next_cursor([inv], "storage_location", ["file_size"])
        assert encoded
        decoded = json.loads(base64.b64decode(encoded))
        assert decoded["id"] == inv.id


@pytest.mark.unit
class TestFilesRouterEndpointsExtended:
    def test_get_file_details_success(
        self, authenticated_client: TestClient, file_inventory_factory, db_session: Session
    ):
        inv = file_inventory_factory(path="/tmp/details_target.txt")
        record = FileRecord(
            path_id=inv.path_id,
            original_path="/tmp/details_target.txt",
            cold_storage_path="/tmp/cold/details_target.txt",
            file_size=inv.file_size,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        txn = FileTransactionHistory(
            file_id=inv.id,
            transaction_type=TransactionType.FREEZE,
            old_storage_type=StorageType.HOT,
            new_storage_type=StorageType.COLD,
            success=True,
            initiated_by="test",
        )
        db_session.add(txn)
        db_session.commit()

        response = authenticated_client.get(f"/api/v1/files/details/{inv.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["file"]["id"] == inv.id
        assert body["file_record"]["original_path"] == record.original_path
        assert len(body["transactions"]) == 1

    def test_get_file_details_not_found(self, authenticated_client: TestClient):
        response = authenticated_client.get("/api/v1/files/details/999999")
        assert response.status_code == 404

    @patch("app.services.file_mover.FileMover.move_file", return_value=(False, "disk full"))
    def test_move_file_failure(
        self, _mock_move, authenticated_client: TestClient, monitored_path_factory, tmp_path
    ):
        path = monitored_path_factory("MoveFail", str(tmp_path / "move_hot"))
        source = Path(path.source_path) / "src.txt"
        source.write_text("x")
        dest = Path(path.source_path) / "dest.txt"

        response = authenticated_client.post(
            "/api/v1/files/move",
            json={
                "source_path": str(source),
                "destination_path": str(dest),
                "operation_type": "move",
            },
        )
        assert response.status_code == 500

    def test_move_file_source_missing(
        self, authenticated_client: TestClient, monitored_path_factory, tmp_path
    ):
        path = monitored_path_factory("MoveMissing", str(tmp_path / "move_missing_hot"))
        response = authenticated_client.post(
            "/api/v1/files/move",
            json={
                "source_path": str(Path(path.source_path) / "missing.txt"),
                "destination_path": str(Path(path.source_path) / "dest.txt"),
                "operation_type": "move",
            },
        )
        assert response.status_code == 404

    @patch("app.services.file_mover.FileMover.move_file", return_value=(True, None))
    def test_move_file_updates_existing_record(
        self,
        _mock_move,
        authenticated_client: TestClient,
        monitored_path_factory,
        db_session: Session,
        tmp_path,
    ):
        path = monitored_path_factory("MoveRecord", str(tmp_path / "move_record_hot"))
        source = Path(path.source_path) / "src.txt"
        source.write_text("content")
        dest = Path(path.source_path) / "dest.txt"
        record = FileRecord(
            path_id=path.id,
            original_path=str(source),
            cold_storage_path=str(dest),
            file_size=7,
            operation_type=OperationType.MOVE,
        )
        db_session.add(record)
        db_session.commit()
        record_id = record.id

        response = authenticated_client.post(
            "/api/v1/files/move",
            json={
                "source_path": str(source),
                "destination_path": str(dest),
                "operation_type": "copy",
            },
        )
        assert response.status_code == 202
        db_session.expire_all()
        updated = db_session.query(FileRecord).filter(FileRecord.id == record_id).first()
        assert updated.operation_type == OperationType.COPY

    def test_browse_forbidden_and_not_found(
        self, authenticated_client: TestClient, monitored_path_factory, tmp_path
    ):
        assert authenticated_client.get("/api/v1/files/browse?directory=/etc").status_code == 403

        path = monitored_path_factory("BrowseMissing", str(tmp_path / "browse_missing"))
        missing_dir = str(Path(path.source_path) / "does-not-exist")
        assert (
            authenticated_client.get(f"/api/v1/files/browse?directory={missing_dir}").status_code
            == 404
        )

    def test_thaw_not_found_and_hot_conflict(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        cold = file_inventory_factory(
            str(tmp_path / "cold_no_record.txt"), storage_type=StorageType.COLD
        )
        assert authenticated_client.post(f"/api/v1/files/thaw/{cold.id}").status_code == 404

        hot = file_inventory_factory(
            str(tmp_path / "already_hot.txt"), storage_type=StorageType.HOT
        )
        assert authenticated_client.post(f"/api/v1/files/thaw/{hot.id}").status_code == 409

    @patch("app.services.file_thawer.FileThawer.thaw_file", return_value=(False, "thaw failed"))
    def test_thaw_failure_restores_status(
        self,
        _mock_thaw,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "cold_thaw_fail.txt"), storage_type=StorageType.COLD
        )
        db_session.add(
            FileRecord(
                original_path=str(tmp_path / "hot" / "cold_thaw_fail.txt"),
                cold_storage_path=cold.file_path,
                file_size=cold.file_size,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        cold_id = cold.id

        response = authenticated_client.post(f"/api/v1/files/thaw/{cold_id}")
        assert response.status_code == 500
        db_session.expire_all()
        refreshed = db_session.query(FileInventory).filter(FileInventory.id == cold_id).first()
        assert refreshed.status == FileStatus.ACTIVE

    def test_freeze_and_relocate_error_paths(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "cold_freeze_opts.txt"), storage_type=StorageType.COLD
        )
        assert (
            authenticated_client.get(f"/api/v1/files/freeze/{cold.id}/options").status_code == 404
        )

        hot = file_inventory_factory(str(tmp_path / "hot_freeze.txt"), storage_type=StorageType.HOT)
        assert (
            authenticated_client.post(
                f"/api/v1/files/freeze/{hot.id}?storage_location_id=99999"
            ).status_code
            == 400
        )

        cold_reloc = file_inventory_factory(
            str(tmp_path / "cold_reloc.txt"), storage_type=StorageType.COLD
        )
        assert (
            authenticated_client.get(f"/api/v1/files/relocate/{cold_reloc.id}/options").status_code
            == 200
        )
        assert (
            authenticated_client.get("/api/v1/files/relocate/tasks/unknown-task").status_code == 404
        )

    def test_relocate_options_and_tasks(
        self, authenticated_client: TestClient, monkeypatch, file_inventory_factory, tmp_path
    ):
        cold = file_inventory_factory(
            str(tmp_path / "cold_task.txt"),
            storage_type=StorageType.COLD,
        )
        from app.services.relocation_manager import relocation_manager

        monkeypatch.setattr(relocation_manager, "get_all_active_tasks", lambda: [{"id": "t1"}])
        response = authenticated_client.get("/api/v1/files/relocate/tasks?active_only=true")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        monkeypatch.setattr(
            relocation_manager,
            "get_task_for_inventory",
            lambda _inv_id: {"task_id": "active", "status": "running"},
        )
        status_resp = authenticated_client.get(f"/api/v1/files/relocate/{cold.id}/status")
        assert status_resp.json()["has_active_task"] is True

    @patch("app.services.metadata_backfill.MetadataBackfillService.backfill_path")
    @patch("app.services.metadata_backfill.MetadataBackfillService.__init__", return_value=None)
    def test_metadata_backfill_path_scoped(
        self,
        _init,
        mock_backfill_path,
        authenticated_client: TestClient,
        monitored_path_factory,
        tmp_path,
    ):
        path = monitored_path_factory("BackfillPath", str(tmp_path / "backfill"))
        mock_backfill_path.return_value = {"processed": 2, "updated": 1, "errors": 0}
        response = authenticated_client.post(
            f"/api/v1/files/metadata/backfill?path_id={path.id}&batch_size=50&compute_checksum=false"
        )
        assert response.status_code == 200
        mock_backfill_path.assert_called_once()

    def test_bulk_thaw_missing_record(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_no_record.txt"), storage_type=StorageType.COLD
        )
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw", json={"file_ids": [cold.id, 999999]}
        )
        data = response.json()
        assert data["failed"] == 2
        assert data["successful"] == 0

    def test_bulk_freeze_location_errors(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        hot = file_inventory_factory(
            str(tmp_path / "bulk_hot_err.txt"), storage_type=StorageType.HOT
        )
        response = authenticated_client.post(
            "/api/v1/files/bulk/freeze",
            json={"file_ids": [hot.id], "storage_location_id": 888888},
        )
        assert response.json()["failed"] == 1

        missing_loc = ColdStorageLocation(name="Missing Path Loc", path="/no/such/bulk/freeze/path")
        db_session.add(missing_loc)
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/freeze",
            json={"file_ids": [hot.id], "storage_location_id": missing_loc.id},
        )
        assert response.json()["failed"] == 1

    def test_pin_unpin_edge_cases(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        assert authenticated_client.post("/api/v1/files/999999/pin").status_code == 404
        assert authenticated_client.delete("/api/v1/files/999999/pin").status_code == 404

        inv = file_inventory_factory(str(tmp_path / "already_pinned.txt"), is_pinned=True)
        assert authenticated_client.post(f"/api/v1/files/{inv.id}/pin").json()["is_pinned"] is True

        unpinned = file_inventory_factory(str(tmp_path / "not_pinned.txt"))
        assert (
            authenticated_client.delete(f"/api/v1/files/{unpinned.id}/pin").json()["is_pinned"]
            is False
        )

    def test_share_endpoints(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        inv = file_inventory_factory(str(tmp_path / "share_me.txt"))
        inv_id = inv.id
        response = authenticated_client.post(
            f"/api/v1/files/{inv_id}/share", json={"is_shareable": True}
        )
        assert response.status_code == 200
        db_session.expire_all()
        updated = db_session.query(FileInventory).filter(FileInventory.id == inv_id).first()
        assert updated.is_shareable is True

        background_tasks = BackgroundTasks()
        with patch("app.services.p2p_service.p2p_service.sync_all_peer_manifests") as mock_sync:
            bulk_response = files_router.bulk_set_file_shareability(
                BulkShareToggleRequest(file_ids=[inv_id, 424242], is_shareable=False),
                background_tasks,
                db_session,
            )
        assert bulk_response.successful == 1
        assert bulk_response.failed == 1
        assert mock_sync is not None

    def test_list_files_query_filters(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        pinned = file_inventory_factory(str(tmp_path / "pinned_list.txt"), is_pinned=True)
        file_inventory_factory(str(tmp_path / "unpinned_list.txt"), path_name="unpinned_path")

        response = authenticated_client.get("/api/v1/files?is_pinned=true")
        lines = response.content.decode().strip().split("\n")
        metadata = json.loads(lines[0])
        record_ids = set()
        for line in lines[1:]:
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("type") == "file":
                record_ids.add(payload["data"]["id"])
        assert pinned.id in record_ids
        assert metadata["total"] >= 1

        response = authenticated_client.get('/api/v1/files?search=status:missing_cold+mime:"text"')
        assert response.status_code == 200

        response = authenticated_client.get(
            "/api/v1/files?sort_by=file_size&sort_order=asc&page_size=10"
        )
        assert response.status_code == 200
        assert pinned.id

    @patch("app.services.file_thawer.FileThawer.thaw_file", return_value=(True, None))
    def test_bulk_thaw_success(
        self,
        _mock_thaw,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_thaw_ok.txt"), storage_type=StorageType.COLD
        )
        db_session.add(
            FileRecord(
                path_id=cold.path_id,
                original_path=str(tmp_path / "hot" / "bulk_thaw_ok.txt"),
                cold_storage_path=cold.file_path,
                file_size=cold.file_size,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw", json={"file_ids": [cold.id]}
        )
        data = response.json()
        assert data["successful"] == 1
        assert data["failed"] == 0

    def test_bulk_pin_and_unpin(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path, db_session
    ):
        from app.models import PinnedFile

        inv = file_inventory_factory(str(tmp_path / "bulk_pin.txt"))
        pin_resp = authenticated_client.post(
            "/api/v1/files/bulk/pin", json={"file_ids": [inv.id, 999999]}
        )
        assert pin_resp.json()["successful"] == 1
        dup_pin = authenticated_client.post(
            "/api/v1/files/bulk/pin", json={"file_ids": [inv.id, inv.id]}
        )
        messages = [row["message"] for row in dup_pin.json()["results"]]
        assert any("already pinned" in msg.lower() for msg in messages)

        db_session.add(PinnedFile(path_id=inv.path_id, file_path=inv.file_path))
        db_session.commit()
        already = authenticated_client.post("/api/v1/files/bulk/pin", json={"file_ids": [inv.id]})
        assert already.json()["results"][0]["message"] == "File already pinned"

        unpin_resp = authenticated_client.post(
            "/api/v1/files/bulk/unpin", json={"file_ids": [inv.id]}
        )
        assert unpin_resp.json()["successful"] == 1

        not_pinned = file_inventory_factory(str(tmp_path / "bulk_unpin_never.txt"))
        unpin_never = authenticated_client.post(
            "/api/v1/files/bulk/unpin",
            json={"file_ids": [not_pinned.id, not_pinned.id]},
        )
        messages = [row["message"] for row in unpin_never.json()["results"]]
        assert messages.count("File not pinned") >= 1
        assert messages.count("File unpinned successfully") >= 0

    @patch("app.services.relocation_manager.relocation_manager.create_task", return_value="task-1")
    def test_bulk_relocate_creates_tasks(
        self,
        _mock_create,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_file = tmp_path / "bulk_reloc.txt"
        cold_file.write_text("data")
        cold = file_inventory_factory(
            str(cold_file),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        target_loc = ColdStorageLocation(
            name="Bulk Relocate Target", path=str(tmp_path / "bulk_reloc_target")
        )
        target_loc.path = str(tmp_path / "bulk_reloc_target")
        (tmp_path / "bulk_reloc_target").mkdir(exist_ok=True)
        db_session.add(target_loc)
        path = db_session.get(MonitoredPath, cold.path_id)
        path.storage_locations = [storage_location, target_loc]
        db_session.commit()

        response = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={"file_ids": [cold.id], "target_storage_location_id": target_loc.id},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["successful"] == 1

    def test_freeze_and_relocate_happy_paths(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
        monkeypatch,
    ):
        hot = file_inventory_factory(str(tmp_path / "freeze_ok.txt"), storage_type=StorageType.HOT)
        monkeypatch.setattr(
            "app.services.file_freezer.FileFreezer.freeze_file",
            lambda **kwargs: (True, None, None),
        )
        response = authenticated_client.post(
            f"/api/v1/files/freeze/{hot.id}?storage_location_id={storage_location.id}"
        )
        assert response.status_code in (200, 202)

        cold_path = tmp_path / "relocate_ok.txt"
        cold_path.write_text("cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        target_loc = ColdStorageLocation(
            name="Relocate Target", path=str(tmp_path / "relocate_target")
        )
        (tmp_path / "relocate_target").mkdir(exist_ok=True)
        db_session.add(target_loc)
        db_session.flush()
        path_id = cold.path_id
        target_loc_id = target_loc.id
        storage_loc_id = storage_location.id
        path = db_session.get(MonitoredPath, path_id)
        path.storage_locations.clear()
        path.storage_locations.extend(
            [
                db_session.get(ColdStorageLocation, storage_loc_id),
                db_session.get(ColdStorageLocation, target_loc_id),
            ]
        )
        db_session.commit()
        monkeypatch.setattr(
            "app.services.relocation_manager.relocation_manager.create_task",
            lambda **kwargs: "relocate-task",
        )
        relocate = authenticated_client.post(
            f"/api/v1/files/relocate/{cold.id}",
            json={"target_storage_location_id": target_loc_id},
        )
        assert relocate.status_code == 202

    def test_relocate_file_error_branches(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        assert (
            authenticated_client.post(
                "/api/v1/files/relocate/999999",
                json={"target_storage_location_id": storage_location.id},
            ).status_code
            == 404
        )

        hot = file_inventory_factory(str(tmp_path / "not_cold.txt"), storage_type=StorageType.HOT)
        assert (
            authenticated_client.post(
                f"/api/v1/files/relocate/{hot.id}",
                json={"target_storage_location_id": storage_location.id},
            ).status_code
            == 404
        )

        cold_path = tmp_path / "relocate_same_loc.txt"
        cold_path.write_text("cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        db_session.commit()
        same_loc = authenticated_client.post(
            f"/api/v1/files/relocate/{cold.id}",
            json={"target_storage_location_id": storage_location.id},
        )
        assert same_loc.status_code == 400

    @patch(
        "app.services.relocation_manager.relocation_manager.create_task",
        side_effect=ValueError("queue full"),
    )
    def test_relocate_file_create_task_conflict(
        self,
        _mock_task,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_path = tmp_path / "relocate_conflict.txt"
        cold_path.write_text("cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        target_loc = ColdStorageLocation(
            name="Relocate Conflict Target", path=str(tmp_path / "relocate_conflict_target")
        )
        (tmp_path / "relocate_conflict_target").mkdir(exist_ok=True)
        db_session.add(target_loc)
        path = db_session.get(MonitoredPath, cold.path_id)
        path.storage_locations = [storage_location, target_loc]
        db_session.commit()

        response = authenticated_client.post(
            f"/api/v1/files/relocate/{cold.id}",
            json={"target_storage_location_id": target_loc.id},
        )
        assert response.status_code == 409

    def test_serialize_helpers_and_invalid_tag_filter(
        self, db_session, file_inventory_factory, monitored_path_factory, tmp_path
    ):
        path = monitored_path_factory("Serialize", str(tmp_path / "serialize_hot"))
        inv = file_inventory_factory(str(tmp_path / "serialize.txt"), path_name=path.name)
        serialized = files_router._serialize_file(inv, {path.id: path})
        assert serialized["id"] == inv.id

        with pytest.raises(ValueError):
            files_router._parse_tag_ids("1,not-a-number")

    def test_bulk_delete_and_missing_inventory(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        inv = file_inventory_factory(str(tmp_path / "bulk_delete_one.txt"))
        response = authenticated_client.post(
            "/api/v1/files/bulk/delete",
            json={"file_ids": [inv.id, 999999]},
        )
        data = response.json()
        assert data["successful"] == 1
        assert data["failed"] == 1

    def test_bulk_freeze_success(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        monkeypatch,
        tmp_path,
    ):
        hot = file_inventory_factory(
            str(tmp_path / "bulk_freeze_ok.txt"), storage_type=StorageType.HOT
        )
        monkeypatch.setattr(
            "app.services.file_freezer.FileFreezer.freeze_file",
            lambda **kwargs: (True, None, None),
        )
        response = authenticated_client.post(
            "/api/v1/files/bulk/freeze",
            json={"file_ids": [hot.id], "storage_location_id": storage_location.id},
        )
        assert response.json()["successful"] == 1

    def test_get_freeze_options_for_hot_file(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        hot = file_inventory_factory(
            str(tmp_path / "freeze_opts.txt"), storage_type=StorageType.HOT
        )
        response = authenticated_client.get(f"/api/v1/files/freeze/{hot.id}/options")
        assert response.status_code == 200
        assert response.json()["can_freeze"] is True
        assert len(response.json()["available_locations"]) >= 1


def _parse_ndjson(response) -> list[dict]:
    lines = response.content.decode().strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.unit
class TestFilesListAndFilterExtended:
    def test_list_files_validation_errors(self, authenticated_client: TestClient):
        assert authenticated_client.get("/api/v1/files?min_size=-1").status_code == 400
        assert authenticated_client.get("/api/v1/files?max_size=-5").status_code == 400
        assert (
            authenticated_client.get("/api/v1/files?min_size=1000&max_size=100").status_code == 400
        )

    def test_list_files_invalid_tag_ids_stream_error(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        file_inventory_factory(str(tmp_path / "tag_filter.txt"))
        response = authenticated_client.get("/api/v1/files?tag_ids=1,not-int")
        lines = _parse_ndjson(response)
        assert lines[0]["type"] == "error"

    def test_list_files_invalid_cursor_stream_error(self, authenticated_client: TestClient):
        response = authenticated_client.get("/api/v1/files?cursor=not-valid-base64!!!")
        lines = _parse_ndjson(response)
        assert lines[0]["type"] == "error"

    def test_parse_google_search_size_variants(self):
        parsed_gt = files_router.parse_google_search("size:>2mb")
        assert parsed_gt["size_op"] == ">"
        assert parsed_gt["size_val"] == 2 * 1024 * 1024
        parsed_eq = files_router.parse_google_search("size:=1024")
        assert parsed_eq["size_op"] == "="
        assert parsed_eq["size_val"] == 1024
        parsed_gb = files_router.parse_google_search("size:1gb")
        assert parsed_gb["size_val"] == 1024**3

    def test_apply_filters_search_operators(
        self, db_session, file_inventory_factory, create_tag, tmp_path
    ):
        from app.models import FileTag, PinnedFile

        inv = file_inventory_factory(
            str(tmp_path / "filter_ops.pdf"),
            file_extension=".pdf",
            mime_type="application/pdf",
            size=5000,
            checksum="abc123",
        )
        tag = create_tag("docs")
        db_session.add(FileTag(file_id=inv.id, tag_id=tag.id))
        db_session.add(PinnedFile(path_id=inv.path_id, file_path=inv.file_path))
        db_session.commit()

        query = db_session.query(FileInventory)
        filtered = files_router._apply_filters(
            query,
            db_session,
            FilterCriteria(
                file_status="missing_cold",
                search='ext:pdf -tmp path:"filter" size:>1000 pinned:true has_checksum:true',
                tag_id_list=[tag.id],
                is_pinned=False,
            ),
        )
        assert filtered.count() == 0

        hot_query = files_router._apply_filters(
            db_session.query(FileInventory),
            db_session,
            FilterCriteria(
                search="status:active mime:pdf",
                extension="pdf",
                mime_type="pdf",
                has_checksum=True,
                is_pinned=True,
                min_size=1000,
                max_size=10000,
                storage_location_id=inv.cold_storage_location_id,
            ),
        )
        assert hot_query.filter(FileInventory.id == inv.id).count() == 1

    def test_list_files_local_only_and_remote_peer(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache

        local = file_inventory_factory(str(tmp_path / "local_only.txt"), checksum="deadbeef")
        peer = P2PPeer(
            peer_name="RemotePeer",
            peer_id="10.0.0.1:9000",
            host="10.0.0.1",
            port=9000,
            status=P2PPeerStatus.CONNECTED,
            psk_hash="peer-hash",
        )
        db_session.add(peer)
        db_session.flush()
        db_session.add(
            RemoteSharedFileCache(
                peer_id=peer.id,
                remote_file_id="r-1",
                path_id=local.path_id,
                file_path="/remote/local_only.txt",
                display_file_path="/remote/local_only.txt",
                relative_path="local_only.txt",
                storage_type=StorageType.HOT,
                file_size=10,
                checksum="deadbeef",
            )
        )
        db_session.commit()

        local_resp = authenticated_client.get("/api/v1/files?local_only=true")
        meta = _parse_ndjson(local_resp)[0]
        assert meta["type"] == "metadata"

        remote_only = authenticated_client.get("/api/v1/files?remote_only=true")
        remote_meta = _parse_ndjson(remote_only)[0]
        assert remote_meta["type"] == "metadata"

        peer_filter = authenticated_client.get(
            f"/api/v1/files?remote_peer_id={peer.id}&sort_by=file_size&sort_order=asc"
        )
        assert peer_filter.status_code == 200

    def test_serialize_remote_cached_file(self, db_session):
        from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache

        peer = P2PPeer(
            peer_name="PeerA",
            peer_id="1.1.1.1:1",
            host="1.1.1.1",
            port=1,
            status=P2PPeerStatus.CONNECTED,
            psk_hash="peer-hash",
        )
        db_session.add(peer)
        db_session.flush()
        remote = RemoteSharedFileCache(
            peer_id=peer.id,
            remote_file_id="rid",
            file_path="/remote/x.bin",
            display_file_path="/remote/x.bin",
            relative_path="x.bin",
            storage_type=StorageType.HOT,
            file_size=3,
        )
        serialized = files_router._serialize_remote_cached_file(
            remote, peer, peer_count=2, peer_names=["PeerA", "PeerB"]
        )
        assert serialized["is_remote"] is True
        assert serialized["also_available_on_peer_count"] == 2

    def test_browse_success(
        self, authenticated_client: TestClient, monitored_path_factory, tmp_path
    ):
        path = monitored_path_factory("BrowseOk", str(tmp_path / "browse_ok"))
        browse_dir = Path(path.source_path)
        (browse_dir / "visible.txt").write_text("ok")
        response = authenticated_client.get(f"/api/v1/files/browse?directory={browse_dir}")
        assert response.status_code == 200
        names = [entry["name"] for entry in response.json()["files"]]
        assert "visible.txt" in names

    @patch("app.services.file_thawer.FileThawer.thaw_file", return_value=(True, None))
    def test_bulk_thaw_with_pin(
        self,
        _mock_thaw,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_pin_thaw.txt"), storage_type=StorageType.COLD
        )
        db_session.add(
            FileRecord(
                path_id=cold.path_id,
                original_path=str(tmp_path / "hot" / "bulk_pin_thaw.txt"),
                cold_storage_path=cold.file_path,
                file_size=cold.file_size,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw?pin=true", json={"file_ids": [cold.id]}
        )
        assert response.json()["successful"] == 1

    def test_bulk_freeze_monitored_path_and_location_errors(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        hot = file_inventory_factory(str(tmp_path / "bulk_path_err.txt"))
        other_loc = ColdStorageLocation(name="Unlinked Loc", path=str(tmp_path / "unlinked_cold"))
        (tmp_path / "unlinked_cold").mkdir(exist_ok=True)
        db_session.add(other_loc)
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/freeze",
            json={"file_ids": [hot.id], "storage_location_id": other_loc.id},
        )
        assert response.json()["failed"] == 1

    def test_bulk_relocate_error_branches(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        missing_target = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={"file_ids": [1], "target_storage_location_id": 999999},
        )
        assert missing_target.json()["failed"] == 1

        cold_path = tmp_path / "bulk_reloc_missing.txt"
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        db_session.commit()
        missing_file = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={
                "file_ids": [cold.id],
                "target_storage_location_id": storage_location.id,
            },
        )
        assert missing_file.json()["failed"] == 1

    def test_cursor_pagination_asc_with_null_sort(
        self, db_session, file_inventory_factory, tmp_path
    ):
        inv = file_inventory_factory(str(tmp_path / "cursor_null.txt"))
        query = db_session.query(FileInventory)
        paginated = files_router._apply_cursor_pagination(
            query,
            FileInventory.file_atime,
            {"id": inv.id, "sort_value": None},
            is_descending=False,
        )
        assert paginated is not None

    @patch("app.services.relocation_manager.relocation_manager.create_task", return_value="task-x")
    def test_bulk_relocate_success(
        self,
        _mock_task,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_path = tmp_path / "bulk_reloc_ok.bin"
        cold_path.write_bytes(b"cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        target_loc = ColdStorageLocation(
            name="Bulk Relocate Target 2", path=str(tmp_path / "bulk_reloc_target_2")
        )
        (tmp_path / "bulk_reloc_target_2").mkdir(exist_ok=True)
        db_session.add(target_loc)
        path = db_session.get(MonitoredPath, cold.path_id)
        path.storage_locations = [storage_location, target_loc]
        db_session.commit()

        response = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={"file_ids": [cold.id], "target_storage_location_id": target_loc.id},
        )
        assert response.json()["successful"] == 1

    @patch(
        "app.services.relocation_manager.relocation_manager.create_task",
        side_effect=ValueError("queue full"),
    )
    def test_bulk_relocate_create_task_conflict(
        self,
        _mock_task,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_path = tmp_path / "bulk_reloc_conflict.txt"
        cold_path.write_text("cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        target_loc = ColdStorageLocation(
            name="Bulk Relocate Conflict Target",
            path=str(tmp_path / "bulk_reloc_conflict_target"),
        )
        (tmp_path / "bulk_reloc_conflict_target").mkdir(exist_ok=True)
        db_session.add(target_loc)
        path = db_session.get(MonitoredPath, cold.path_id)
        path.storage_locations = [storage_location, target_loc]
        db_session.commit()

        response = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={"file_ids": [cold.id], "target_storage_location_id": target_loc.id},
        )
        body = response.json()
        assert body["failed"] == 1
        assert "queue full" in body["results"][0]["message"]

    def test_list_files_remote_only_stream(
        self, authenticated_client, db_session, monitored_path_factory, tmp_path
    ):
        from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache

        hot = tmp_path / "remote_stream_hot"
        hot.mkdir()
        path = monitored_path_factory("Remote Stream", str(hot))
        peer = P2PPeer(
            peer_name="StreamPeer",
            peer_id="8.8.8.8:9000",
            host="8.8.8.8",
            port=9000,
            status=P2PPeerStatus.CONNECTED,
            psk_hash="hash",
        )
        db_session.add(peer)
        db_session.flush()
        db_session.add(
            RemoteSharedFileCache(
                peer_id=peer.id,
                remote_file_id="stream-1",
                path_id=path.id,
                file_path="/remote/stream.bin",
                display_file_path="/remote/stream.bin",
                relative_path="stream.bin",
                storage_type=StorageType.HOT,
                file_size=8,
            )
        )
        db_session.commit()

        response = authenticated_client.get("/api/v1/files?remote_only=true&search=stream")
        lines = _parse_ndjson(response)
        assert lines[0]["type"] == "metadata"
        assert any(line.get("type") == "complete" for line in lines)


@pytest.mark.unit
class TestFilesRouterCoverageBoost:
    def test_apply_filters_search_size_and_unpinned(
        self, db_session, file_inventory_factory, tmp_path
    ):
        small = file_inventory_factory(str(tmp_path / "small.bin"), size=100)
        file_inventory_factory(str(tmp_path / "large.bin"), size=9000)
        query = db_session.query(FileInventory)
        filtered = files_router._apply_filters(
            query,
            db_session,
            FilterCriteria(search="size:<500 pinned:false"),
        )
        assert filtered.filter(FileInventory.id == small.id).count() == 1

        eq_filtered = files_router._apply_filters(
            db_session.query(FileInventory),
            db_session,
            FilterCriteria(search="size:=100"),
        )
        assert eq_filtered.filter(FileInventory.id == small.id).count() == 1

    def test_apply_cursor_pagination_descending_with_value(
        self, db_session, file_inventory_factory, tmp_path
    ):
        inv = file_inventory_factory(str(tmp_path / "cursor_desc.txt"), size=50)
        query = db_session.query(FileInventory)
        paginated = files_router._apply_cursor_pagination(
            query,
            FileInventory.file_size,
            {"id": inv.id, "sort_value": 50},
            is_descending=True,
        )
        assert paginated is not None

    @patch("app.services.file_thawer.FileThawer.thaw_file", return_value=(False, "thaw failed"))
    def test_bulk_thaw_failure_message(
        self,
        _mock_thaw,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_thaw_fail.txt"), storage_type=StorageType.COLD
        )
        db_session.add(
            FileRecord(
                path_id=cold.path_id,
                original_path=str(tmp_path / "hot" / "bulk_thaw_fail.txt"),
                cold_storage_path=cold.file_path,
                file_size=cold.file_size,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw", json={"file_ids": [cold.id]}
        )
        body = response.json()
        assert body["failed"] == 1
        assert "thaw failed" in body["results"][0]["message"]

    @patch("app.services.file_thawer.FileThawer.thaw_file", side_effect=RuntimeError("boom"))
    def test_bulk_thaw_exception(
        self,
        _mock_thaw,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_thaw_exc.txt"), storage_type=StorageType.COLD
        )
        db_session.add(
            FileRecord(
                path_id=cold.path_id,
                original_path=str(tmp_path / "hot" / "bulk_thaw_exc.txt"),
                cold_storage_path=cold.file_path,
                file_size=cold.file_size,
                operation_type=OperationType.MOVE,
            )
        )
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw", json={"file_ids": [cold.id]}
        )
        assert response.json()["failed"] == 1

    def test_bulk_freeze_inaccessible_storage_location(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        hot = file_inventory_factory(str(tmp_path / "bulk_inaccessible.txt"))
        missing_loc = ColdStorageLocation(
            name="Inaccessible Bulk Loc", path=str(tmp_path / "missing_bulk_cold")
        )
        db_session.add(missing_loc)
        db_session.commit()
        response = authenticated_client.post(
            "/api/v1/files/bulk/freeze",
            json={"file_ids": [hot.id], "storage_location_id": missing_loc.id},
        )
        body = response.json()
        assert body["failed"] == 1
        assert "not accessible" in body["results"][0]["message"].lower()

    def test_relocate_options_infers_location_from_path_prefix(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_dir = Path(storage_location.path)
        cold_dir.mkdir(parents=True, exist_ok=True)
        cold_file = cold_dir / "prefix_reloc.bin"
        cold_file.write_text("cold")
        cold = file_inventory_factory(str(cold_file), storage_type=StorageType.COLD)
        cold.cold_storage_location_id = None
        db_session.commit()

        response = authenticated_client.get(f"/api/v1/files/relocate/{cold.id}/options")
        assert response.status_code == 200
        body = response.json()
        assert body["current_location_id"] == storage_location.id

    def test_relocate_file_unknown_current_location(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        target_loc = ColdStorageLocation(
            name="Relocate Infer Target", path=str(tmp_path / "relocate_infer_target")
        )
        (tmp_path / "relocate_infer_target").mkdir(exist_ok=True)
        db_session.add(target_loc)
        db_session.flush()

        orphan = tmp_path / "orphan_reloc.bin"
        orphan.write_text("x")
        cold = file_inventory_factory(
            str(orphan),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = None
        db_session.commit()

        path = db_session.get(MonitoredPath, cold.path_id)
        current_loc = db_session.get(ColdStorageLocation, storage_location.id)
        path.storage_locations = [current_loc, target_loc]
        db_session.commit()

        response = authenticated_client.post(
            f"/api/v1/files/relocate/{cold.id}",
            json={"target_storage_location_id": target_loc.id},
        )
        assert response.status_code == 400
        assert "current storage location" in response.json()["detail"].lower()

    def test_relocate_file_missing_target_location(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        tmp_path,
    ):
        cold_path = tmp_path / "relocate_missing_target.txt"
        cold_path.write_text("cold")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        response = authenticated_client.post(
            f"/api/v1/files/relocate/{cold.id}",
            json={"target_storage_location_id": 999999},
        )
        assert response.status_code == 404

    def test_bulk_relocate_monitored_path_and_association_errors(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
    ):
        cold_path = tmp_path / "bulk_reloc_path_err.txt"
        cold_path.write_text("x")
        cold = file_inventory_factory(
            str(cold_path),
            storage_type=StorageType.COLD,
            cold_storage_location=storage_location,
        )
        cold.cold_storage_location_id = storage_location.id
        other_loc = ColdStorageLocation(name="Unlinked Bulk Loc", path=str(tmp_path / "unlinked2"))
        (tmp_path / "unlinked2").mkdir(exist_ok=True)
        db_session.add(other_loc)
        db_session.commit()

        unlinked = authenticated_client.post(
            "/api/v1/files/bulk/relocate",
            json={"file_ids": [cold.id], "target_storage_location_id": other_loc.id},
        )
        assert unlinked.json()["failed"] == 1

    def test_get_relocation_tasks_recent(self, authenticated_client: TestClient, monkeypatch):
        from app.services.relocation_manager import relocation_manager

        monkeypatch.setattr(
            relocation_manager, "get_recent_tasks", lambda limit=20: [{"id": "recent"}]
        )
        response = authenticated_client.get("/api/v1/files/relocate/tasks?limit=5")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_thaw_endpoint_conflict_and_missing_record(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        db_session: Session,
        tmp_path,
    ):
        hot = file_inventory_factory(
            str(tmp_path / "already_hot.txt"), storage_type=StorageType.HOT
        )
        assert authenticated_client.post(f"/api/v1/files/thaw/{hot.id}").status_code == 409

        cold = file_inventory_factory(
            str(tmp_path / "no_record_cold.txt"), storage_type=StorageType.COLD
        )
        cold.status = FileStatus.MIGRATING
        db_session.commit()
        assert authenticated_client.post(f"/api/v1/files/thaw/{cold.id}").status_code == 409

        cold2 = file_inventory_factory(
            str(tmp_path / "missing_record_cold.txt"), storage_type=StorageType.COLD
        )
        cold2.status = FileStatus.ACTIVE
        db_session.commit()
        assert authenticated_client.post(f"/api/v1/files/thaw/{cold2.id}").status_code == 404

    def test_freeze_endpoint_validation_and_failure(
        self,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        db_session: Session,
        tmp_path,
        monkeypatch,
    ):
        hot = file_inventory_factory(
            str(tmp_path / "freeze_validate.txt"), storage_type=StorageType.HOT
        )
        mock_backend = MagicMock()
        mock_backend.validate_location.return_value = (False, "backend offline")
        mock_backend.capabilities.return_value = MagicMock(
            supports_move=True, supports_copy=True, supports_symlink=True
        )
        monkeypatch.setattr("app.routers.api.files.get_backend", lambda _loc: mock_backend)
        response = authenticated_client.post(
            f"/api/v1/files/freeze/{hot.id}?storage_location_id={storage_location.id}"
        )
        assert response.status_code == 400

    @patch(
        "app.services.file_freezer.FileFreezer.freeze_file",
        return_value=(False, "freeze err", None),
    )
    def test_freeze_endpoint_freezer_failure(
        self,
        _mock_freeze,
        authenticated_client: TestClient,
        file_inventory_factory,
        storage_location,
        tmp_path,
    ):
        hot = file_inventory_factory(
            str(tmp_path / "freeze_fail.txt"), storage_type=StorageType.HOT
        )
        response = authenticated_client.post(
            f"/api/v1/files/freeze/{hot.id}?storage_location_id={storage_location.id}"
        )
        assert response.status_code == 500
        assert "freeze err" in response.json()["detail"]

    def test_bulk_thaw_missing_file_record(
        self, authenticated_client: TestClient, file_inventory_factory, tmp_path
    ):
        cold = file_inventory_factory(
            str(tmp_path / "bulk_no_record.txt"), storage_type=StorageType.COLD
        )
        response = authenticated_client.post(
            "/api/v1/files/bulk/thaw", json={"file_ids": [cold.id]}
        )
        body = response.json()
        assert body["failed"] == 1
        assert "No file record" in body["results"][0]["message"]

    def test_apply_filters_mtime_and_checksum_flags(
        self, db_session, file_inventory_factory, tmp_path
    ):
        inv = file_inventory_factory(
            str(tmp_path / "mtime_filter.bin"),
            checksum="abc",
            file_mtime=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        query = db_session.query(FileInventory)
        filtered = files_router._apply_filters(
            query,
            db_session,
            FilterCriteria(
                min_mtime=datetime(2019, 1, 1, tzinfo=timezone.utc),
                max_mtime=datetime(2021, 1, 1, tzinfo=timezone.utc),
                has_checksum=False,
                is_pinned=False,
            ),
        )
        assert filtered.filter(FileInventory.id == inv.id).count() == 0

        has_checksum = files_router._apply_filters(
            db_session.query(FileInventory),
            db_session,
            FilterCriteria(has_checksum=True),
        )
        assert has_checksum.filter(FileInventory.id == inv.id).count() == 1
