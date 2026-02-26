import pytest

from app.models import Tag, TagRule


@pytest.mark.unit
class TestTagRulesRouter:
    def test_list_tag_rules_success(self, authenticated_client, db_session, create_tag):
        """Test listing all tag rules."""
        tag = create_tag("Rule List Tag")
        rule = TagRule(
            tag_id=tag.id, 
            criterion_type="extension", 
            operator="=", 
            value="txt",
            priority=5
        )
        db_session.add(rule)
        db_session.commit()
        
        response = authenticated_client.get("/api/v1/tag-rules")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(r["value"] == "txt" for r in data)

    def test_create_tag_rule_success(self, authenticated_client, create_tag):
        """Test successful creation of a tag rule."""
        tag = create_tag("Create Rule Tag")
        payload = {
            "tag_id": tag.id,
            "criterion_type": "extension",
            "operator": "=",
            "value": "pdf",
            "enabled": True,
            "priority": 10
        }
        response = authenticated_client.post("/api/v1/tag-rules", json=payload)
        assert response.status_code == 201
        assert response.json()["value"] == "pdf"
        assert response.json()["tag_id"] == tag.id

    def test_create_tag_rule_tag_not_found(self, authenticated_client):
        """Test creating a rule for non-existent tag."""
        payload = {
            "tag_id": 9999,
            "criterion_type": "extension",
            "operator": "=",
            "value": "pdf"
        }
        response = authenticated_client.post("/api/v1/tag-rules", json=payload)
        assert response.status_code == 404

    def test_get_tag_rule_success(self, authenticated_client, db_session, create_tag):
        """Test getting a specific tag rule."""
        tag = create_tag("Get Rule Tag")
        rule = TagRule(tag_id=tag.id, criterion_type="extension", operator="=", value="doc")
        db_session.add(rule)
        db_session.commit()
        rule_id = rule.id
        
        response = authenticated_client.get(f"/api/v1/tag-rules/{rule_id}")
        assert response.status_code == 200
        assert response.json()["value"] == "doc"

    def test_update_tag_rule_success(self, authenticated_client, db_session, create_tag):
        """Test updating a tag rule."""
        tag = create_tag("Update Rule Tag")
        rule = TagRule(tag_id=tag.id, criterion_type="extension", operator="=", value="old")
        db_session.add(rule)
        db_session.commit()
        rule_id = rule.id
        
        payload = {"value": "new", "priority": 20}
        response = authenticated_client.patch(f"/api/v1/tag-rules/{rule_id}", json=payload)
        assert response.status_code == 200
        assert response.json()["value"] == "new"
        assert response.json()["priority"] == 20

    def test_delete_tag_rule_success(self, authenticated_client, db_session, create_tag):
        """Test deleting a tag rule."""
        tag = create_tag("Del Rule Tag")
        rule = TagRule(tag_id=tag.id, criterion_type="extension", operator="=", value="todel")
        db_session.add(rule)
        db_session.commit()
        rule_id = rule.id
        
        response = authenticated_client.delete(f"/api/v1/tag-rules/{rule_id}")
        assert response.status_code == 204
        assert db_session.get(TagRule, rule_id) is None

    def test_apply_tag_rules_success(self, authenticated_client, monkeypatch):
        """Test triggering rule application to all files."""
        from app.services.tag_rule_service import TagRuleService
        mock_apply = lambda self: {"files_processed": 10, "tags_added": 5}
        monkeypatch.setattr(TagRuleService, "apply_all_rules", mock_apply)
        
        response = authenticated_client.post("/api/v1/tag-rules/apply")
        assert response.status_code == 200
        assert response.json()["tags_added"] == 5
