"""Additional monitored path router tests."""

from unittest.mock import MagicMock, patch

import pytest

from app.models import Criteria, CriterionType, Operator


@pytest.mark.unit
def test_create_path_missing_storage_location_ids(authenticated_client, tmp_path, storage_location):
    hot = tmp_path / "hot_missing_storage"
    hot.mkdir()
    payload = {
        "name": "Missing Storage IDs",
        "source_path": str(hot),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id, 999999],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.unit
def test_hot_storage_stats_not_found_path(authenticated_client, db_session):
    from app.models import MonitoredPath

    ghost = MonitoredPath(
        name="Ghost Path",
        source_path="/tmp/definitely-not-a-real-path-for-stats-xyz",
        check_interval_seconds=3600,
    )
    db_session.add(ghost)
    db_session.commit()
    response = authenticated_client.get("/api/v1/paths/stats")
    assert response.status_code == 200
    assert any(item.get("error") for item in response.json())


@pytest.mark.unit
def test_hot_storage_stats_not_a_directory(authenticated_client, db_session, tmp_path):
    from app.models import MonitoredPath

    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("x")
    path = MonitoredPath(
        name="File Not Dir",
        source_path=str(file_path),
        check_interval_seconds=3600,
    )
    db_session.add(path)
    db_session.commit()
    response = authenticated_client.get("/api/v1/paths/stats")
    assert response.status_code == 200
    assert any("Not a directory" in (row.get("error") or "") for row in response.json())


@pytest.mark.unit
def test_validate_path_configuration_atime_failure(
    monitored_path_factory, db_session, storage_location, tmp_path
):
    from fastapi import HTTPException

    from app.models import MonitoredPath
    from app.routers.api.paths import validate_path_configuration

    hot = tmp_path / "hot_atime"
    hot.mkdir()
    path = monitored_path_factory("ATIME Path", str(hot))
    db_session.add(
        Criteria(
            path_id=path.id,
            criterion_type=CriterionType.ATIME,
            operator=Operator.LT,
            value="60",
            enabled=True,
        )
    )
    db_session.commit()
    db_session.refresh(path)

    with patch(
        "app.routers.api.paths.check_atime_availability", return_value=(False, "atime unsupported")
    ), pytest.raises(HTTPException) as exc:
        validate_path_configuration(db_session.get(MonitoredPath, path.id), db_session)
    assert "ATIME criteria" in exc.value.detail


@pytest.mark.unit
def test_stop_scan_when_idle(authenticated_client, monitored_path_factory, tmp_path):
    path = monitored_path_factory("Stop Scan Path", str(tmp_path / "hot_stop"))
    response = authenticated_client.post(f"/api/v1/paths/{path.id}/scan/stop")
    assert response.status_code == 409


@pytest.mark.unit
def test_create_path_validation_failure_rolls_back(
    authenticated_client, tmp_path, storage_location, monkeypatch
):
    from fastapi import HTTPException

    from app.routers.api import paths as paths_router

    hot = tmp_path / "create_validation_fail"
    hot.mkdir()
    monkeypatch.setattr(
        paths_router,
        "validate_path_configuration",
        MagicMock(side_effect=HTTPException(status_code=400, detail="invalid config")),
    )
    payload = {
        "name": "Create Validation Fail",
        "source_path": str(hot),
        "check_interval_seconds": 3600,
        "storage_location_ids": [storage_location.id],
    }
    response = authenticated_client.post("/api/v1/paths", json=payload)
    assert response.status_code == 400


@pytest.mark.unit
def test_update_path_error_branches(
    authenticated_client, monitored_path_factory, storage_location, tmp_path, db_session
):
    path = monitored_path_factory("Update Errors", str(tmp_path / "update_errors_hot"))
    other = monitored_path_factory("Other Path Name", str(tmp_path / "other_hot"))
    path_id = path.id
    other_name = other.name
    storage_loc_id = storage_location.id

    assert authenticated_client.put("/api/v1/paths/999999", json={"name": "X"}).status_code == 404

    dup = authenticated_client.put(f"/api/v1/paths/{path_id}", json={"name": other_name})
    assert dup.status_code == 400

    missing_storage = authenticated_client.put(
        f"/api/v1/paths/{path_id}",
        json={"storage_location_ids": [storage_loc_id, 999999]},
    )
    assert missing_storage.status_code == 400


@pytest.mark.unit
def test_get_path_not_found(authenticated_client):
    assert authenticated_client.get("/api/v1/paths/999999").status_code == 404
