import pytest

from app.models import User


@pytest.mark.unit
class TestUsersRouter:
    def test_list_users_success(self, authenticated_client):
        """Test listing all users."""
        response = authenticated_client.get("/api/v1/users")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(u["username"] == "authtestuser" for u in data)

    def test_create_user_success(self, authenticated_client, db_session):
        """Test creating a new user."""
        payload = {"username": "newuserapi", "password": "password123"}
        response = authenticated_client.post("/api/v1/users", json=payload)
        assert response.status_code == 201
        assert response.json()["username"] == "newuserapi"
        assert "viewer" in response.json()["roles"]

    def test_create_user_duplicate(self, authenticated_client, db_session):
        """Test creating a user with an existing username."""
        payload = {"username": "authtestuser", "password": "password123"}
        response = authenticated_client.post("/api/v1/users", json=payload)
        assert response.status_code == 400
        assert "already taken" in response.json()["detail"].lower()

    def test_update_user_roles_success(self, authenticated_client, db_session):
        """Test updating a user's roles."""
        # Create a target user
        user = User(username="roleuser", password_hash="hash", roles=["viewer"])
        db_session.add(user)
        db_session.commit()
        user_id = user.id
        
        payload = ["viewer", "editor"]
        response = authenticated_client.put(f"/api/v1/users/{user_id}/roles", json=payload)
        assert response.status_code == 200
        assert set(response.json()["roles"]) == {"viewer", "editor"}

    def test_update_user_roles_not_found(self, authenticated_client):
        """Test updating roles for non-existent user."""
        response = authenticated_client.put("/api/v1/users/9999/roles", json=["admin"])
        assert response.status_code == 404

    def test_update_own_roles_prevent_lockout(self, authenticated_client, db_session):
        """Test that an admin cannot remove their own admin role."""
        # Get the current user ID (authtestuser)
        user = db_session.query(User).filter_by(username="authtestuser").first()
        user_id = user.id
        
        response = authenticated_client.put(f"/api/v1/users/{user_id}/roles", json=["viewer"])
        assert response.status_code == 400
        assert "cannot remove admin role from yourself" in response.json()["detail"].lower()

    def test_delete_user_success(self, authenticated_client, db_session):
        """Test deleting a user."""
        user = User(username="deleteuser", password_hash="hash", roles=["viewer"])
        db_session.add(user)
        db_session.commit()
        user_id = user.id
        
        response = authenticated_client.delete(f"/api/v1/users/{user_id}")
        assert response.status_code == 204
        assert db_session.get(User, user_id) is None

    def test_delete_self_prevented(self, authenticated_client, db_session):
        """Test that a user cannot delete themselves."""
        user = db_session.query(User).filter_by(username="authtestuser").first()
        user_id = user.id
        
        response = authenticated_client.delete(f"/api/v1/users/{user_id}")
        assert response.status_code == 400
        assert "cannot delete yourself" in response.json()["detail"].lower()
