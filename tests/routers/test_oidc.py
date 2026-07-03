import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import User, InstanceMetadata
from app.services.instance_config_service import instance_config_service


def test_oidc_disabled_by_default(client: TestClient):
    """Test that OIDC routes return 400 when OIDC is disabled."""
    response = client.get("/api/v1/auth/oidc/login")
    assert response.status_code == 400
    assert "not enabled" in response.json()["detail"]


def test_oidc_check_status_defaults(client: TestClient):
    """Test that auth check reflects OIDC status."""
    response = client.get("/api/v1/auth/check")
    assert response.status_code == 200
    data = response.json()
    assert data["oidc_enabled"] is False
    assert data["oidc_provider_name"] == "Authentik"


def test_oidc_settings_update_and_config(authenticated_client: TestClient):
    """Test updating OIDC settings via PUT and fetching via config endpoint."""
    payload = {
        "oidc_enabled": True,
        "oidc_issuer": "https://authentik.test/app/",
        "oidc_client_id": "test_client_id",
        "oidc_client_secret": "test_secret",
        "oidc_redirect_uri": "https://fridge.test/callback",
        "oidc_provider_name": "Test OIDC",
        "oidc_roles_claim": "groups",
        "oidc_admin_group": "admin-group",
        "oidc_manager_group": "manager-group",
        "oidc_viewer_group": "viewer-group",
        "oidc_default_roles": "viewer",
    }

    response = authenticated_client.put("/api/v1/settings/oidc", json=payload)
    assert response.status_code == 200
    assert response.json()["message"] == "OIDC configuration updated successfully"

    response = authenticated_client.get("/api/v1/settings/config")
    assert response.status_code == 200
    data = response.json()

    oidc = data["oidc"]
    assert oidc["oidc_enabled"]["value"] is True
    assert oidc["oidc_issuer"]["value"] == "https://authentik.test/app/"
    assert oidc["oidc_client_id"]["value"] == "test_client_id"
    assert oidc["oidc_provider_name"]["value"] == "Test OIDC"
    assert oidc["is_env_configured"] is False


@pytest.mark.asyncio
async def test_oidc_login_redirect(client: TestClient, db_session: Session):
    """Test login endpoint redirects to provider authorization endpoint."""
    metadata = InstanceMetadata(
        instance_uuid="test-uuid",
        oidc_enabled=True,
        oidc_issuer="https://authentik.test/app",
        oidc_client_id="test_client_id",
        oidc_provider_name="Test Provider",
    )
    db_session.add(metadata)
    db_session.commit()

    mock_discovery = {
        "authorization_endpoint": "https://authentik.test/app/authorize",
        "token_endpoint": "https://authentik.test/app/token",
        "userinfo_endpoint": "https://authentik.test/app/userinfo",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_discovery

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
        assert response.status_code == 307

        location = response.headers.get("location")
        assert location is not None
        assert "https://authentik.test/app/authorize" in location
        assert "client_id=test_client_id" in location
        assert "scope=openid+profile+email" in location
        assert "state=" in location
        assert "oidc_state" in response.cookies


@pytest.mark.asyncio
async def test_oidc_callback_success(client: TestClient, db_session: Session):
    """Test successful callback processing and user mapping."""
    metadata = InstanceMetadata(
        instance_uuid="test-uuid",
        oidc_enabled=True,
        oidc_issuer="https://authentik.test/app",
        oidc_client_id="test_client_id",
        oidc_client_secret="test_secret",
        oidc_roles_claim="groups",
        oidc_admin_group="super-admins",
        oidc_default_roles="viewer",
    )
    db_session.add(metadata)
    db_session.commit()

    mock_discovery = {
        "authorization_endpoint": "https://authentik.test/app/authorize",
        "token_endpoint": "https://authentik.test/app/token",
        "userinfo_endpoint": "https://authentik.test/app/userinfo",
    }

    mock_tokens = {"access_token": "mock_access_token", "id_token": "mock_id_token"}

    mock_userinfo = {
        "sub": "user_12345",
        "preferred_username": "oidcuser",
        "email": "oidcuser@test.local",
        "groups": ["super-admins", "other-group"],
    }

    mock_response_discovery = MagicMock()
    mock_response_discovery.status_code = 200
    mock_response_discovery.json.return_value = mock_discovery

    mock_response_token = MagicMock()
    mock_response_token.status_code = 200
    mock_response_token.json.return_value = mock_tokens

    mock_response_userinfo = MagicMock()
    mock_response_userinfo.status_code = 200
    mock_response_userinfo.json.return_value = mock_userinfo

    async def mock_get(url, *args, **kwargs):
        if ".well-known/openid-configuration" in url:
            return mock_response_discovery
        elif "userinfo" in url:
            return mock_response_userinfo
        return MagicMock(status_code=404)

    async def mock_post(url, *args, **kwargs):
        if "token" in url:
            return mock_response_token
        return MagicMock(status_code=404)

    client.cookies.set("oidc_state", "teststate")

    with patch("httpx.AsyncClient.get", side_effect=mock_get), patch(
        "httpx.AsyncClient.post", side_effect=mock_post
    ):
        response = client.get("/api/v1/auth/oidc/callback?code=mockcode&state=teststate")
        assert response.status_code == 200
        assert "sessionStorage.setItem('auth_token'" in response.text

        user = db_session.query(User).filter_by(username="oidcuser").first()
        assert user is not None
        assert user.is_active is True
        assert "admin" in user.roles
