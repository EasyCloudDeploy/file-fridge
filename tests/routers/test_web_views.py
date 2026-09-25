"""Smoke coverage for HTML template routes in app.routers.web.views."""

import pytest


@pytest.mark.unit
def test_login_page_renders(authenticated_client):
    response = authenticated_client.get("/login", follow_redirects=False)
    assert response.status_code == 200
    assert "login" in response.text.lower()


@pytest.mark.unit
def test_dashboard_page_renders(authenticated_client):
    response = authenticated_client.get("/")
    assert response.status_code == 200


@pytest.mark.unit
def test_files_page_renders(authenticated_client):
    response = authenticated_client.get("/files")
    assert response.status_code == 200


@pytest.mark.unit
def test_paths_detail_and_edit_pages(authenticated_client, monitored_path_factory):
    path = monitored_path_factory("Web Path", "/tmp/web-path-hot")
    detail = authenticated_client.get(f"/paths/{path.id}")
    assert detail.status_code == 200
    edit = authenticated_client.get(f"/paths/{path.id}/edit")
    assert edit.status_code == 200
    criteria = authenticated_client.get(f"/paths/{path.id}/criteria/new")
    assert criteria.status_code == 200


@pytest.mark.unit
def test_storage_detail_and_edit_pages(authenticated_client, storage_location):
    detail = authenticated_client.get(f"/storage-locations/{storage_location.id}")
    assert detail.status_code == 200
    edit = authenticated_client.get(f"/storage-locations/{storage_location.id}/edit")
    assert edit.status_code == 200
