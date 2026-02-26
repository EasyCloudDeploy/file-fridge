import pytest
from unittest.mock import patch, MagicMock

from app.models import Criteria, CriterionType, Operator, MonitoredPath


@pytest.mark.unit
class TestCriteriaRouter:
    def test_list_criteria_success(self, authenticated_client, monitored_path_factory):
        """Test listing criteria for a path."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit")
        response = authenticated_client.get(f"/api/v1/criteria/path/{path.id}")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_criteria_not_found(self, authenticated_client):
        """Test listing criteria for non-existent path."""
        response = authenticated_client.get("/api/v1/criteria/path/9999")
        assert response.status_code == 404

    def test_create_criteria_success(self, authenticated_client, monitored_path_factory):
        """Test creating a new criterion."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit_create")
        payload = {
            "criterion_type": "mtime",
            "operator": ">",
            "value": "30",
            "enabled": True
        }
        response = authenticated_client.post(f"/api/v1/criteria/path/{path.id}", json=payload)
        assert response.status_code == 201
        assert response.json()["criterion_type"] == "mtime"
        assert response.json()["value"] == "30"

    def test_get_criteria_success(self, authenticated_client, db_session, monitored_path_factory):
        """Test getting a specific criterion."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit_get")
        crit = Criteria(
            path_id=path.id, 
            criterion_type=CriterionType.SIZE, 
            operator=Operator.GT, 
            value="100"
        )
        db_session.add(crit)
        db_session.commit()
        
        response = authenticated_client.get(f"/api/v1/criteria/{crit.id}")
        assert response.status_code == 200
        assert response.json()["value"] == "100"

    def test_update_criteria_success(self, authenticated_client, db_session, monitored_path_factory):
        """Test updating a criterion."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit_upd")
        crit = Criteria(
            path_id=path.id, 
            criterion_type=CriterionType.SIZE, 
            operator=Operator.GT, 
            value="100"
        )
        db_session.add(crit)
        db_session.commit()
        
        payload = {"value": "200", "operator": "<"}
        response = authenticated_client.put(f"/api/v1/criteria/{crit.id}", json=payload)
        assert response.status_code == 200
        assert response.json()["value"] == "200"
        assert response.json()["operator"] == "<"

    def test_delete_criteria_success(self, authenticated_client, db_session, monitored_path_factory):
        """Test deleting a criterion."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit_del")
        crit = Criteria(
            path_id=path.id, 
            criterion_type=CriterionType.SIZE, 
            operator=Operator.GT, 
            value="100"
        )
        db_session.add(crit)
        db_session.commit()
        crit_id = crit.id
        
        response = authenticated_client.delete(f"/api/v1/criteria/{crit_id}")
        assert response.status_code == 204
        assert db_session.get(Criteria, crit_id) is None

    def test_delete_last_criteria_triggers_reversal(self, authenticated_client, db_session, monitored_path_factory, monkeypatch):
        """Test that deleting the last enabled criterion triggers path reversal."""
        path = monitored_path_factory("Test Path", "/tmp/hot_crit_rev")
        path_id = path.id
        crit = Criteria(
            path_id=path_id, 
            criterion_type=CriterionType.SIZE, 
            operator=Operator.GT, 
            value="100",
            enabled=True
        )
        db_session.add(crit)
        db_session.commit()
        crit_id = crit.id
        
        # Mock PathReverser
        from app.services.path_reverser import PathReverser
        reversed_called = []
        def mock_reverse(pid, db):
            reversed_called.append(pid)
            return {"files_reversed": 0, "errors": []}
        
        monkeypatch.setattr(PathReverser, "reverse_path_operations", mock_reverse)
        
        response = authenticated_client.delete(f"/api/v1/criteria/{crit_id}")
        assert response.status_code == 204
        assert path_id in reversed_called

    @patch("app.routers.api.criteria.check_atime_availability")
    def test_create_atime_criteria_incompatible(self, mock_atime, authenticated_client, monitored_path_factory):
        """Test that creating an ATIME criterion fails if atime is not supported on the path."""
        path = monitored_path_factory("Atime Path", "/tmp/hot_atime")
        
        # Mock atime not available
        mock_atime.return_value = (False, "Atime not supported")
        
        payload = {
            "criterion_type": "atime",
            "operator": ">",
            "value": "30",
            "enabled": True
        }
        response = authenticated_client.post(f"/api/v1/criteria/path/{path.id}", json=payload)
        assert response.status_code == 400
        assert "atime not supported" in response.json()["detail"].lower()
