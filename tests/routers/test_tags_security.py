from fastapi.testclient import TestClient
from app.main import app
from app.security import get_current_user

class MockUser:
    id = 1
    username = "admin"
    roles = ["admin"]
    is_active = True

def test_create_tag_invalid_color_xss(client):
    """Test that creating a tag with an invalid color string (potential XSS) fails."""
    # Mock admin user to bypass permission checks
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    try:
        payload = {
            "name": "xss-test-color",
            "color": "#ffffff; <script>alert(1)</script>"
        }

        response = client.post("/api/v1/tags", json=payload)

        # Should fail validation
        assert response.status_code == 422
        # Verify error message detail if possible, but status code is primary check
    finally:
        # Clean up override
        if get_current_user in app.dependency_overrides:
            del app.dependency_overrides[get_current_user]

def test_update_tag_invalid_color_xss(client):
    """Test that updating a tag with an invalid color string fails."""
    # Mock admin user
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    try:
        # First create a valid tag
        payload = {
            "name": "valid-tag",
            "color": "#ffffff"
        }
        response = client.post("/api/v1/tags", json=payload)
        assert response.status_code == 201
        tag_id = response.json()["id"]

        # Try to update with invalid color
        update_payload = {
            "color": "#ffffff; <script>alert(1)</script>"
        }
        response = client.patch(f"/api/v1/tags/{tag_id}", json=update_payload)

        assert response.status_code == 422
    finally:
         if get_current_user in app.dependency_overrides:
            del app.dependency_overrides[get_current_user]

def test_create_tag_valid_color(client):
    """Test that creating a tag with a valid color succeeds."""
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    try:
        payload = {
            "name": "valid-color-tag",
            "color": "#FF5733"
        }

        response = client.post("/api/v1/tags", json=payload)

        assert response.status_code == 201
        assert response.json()["color"] == "#FF5733"
    finally:
         if get_current_user in app.dependency_overrides:
            del app.dependency_overrides[get_current_user]
