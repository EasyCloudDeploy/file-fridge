import pytest

from app.models import Tag, FileTag, FileInventory


@pytest.mark.unit
class TestTagsRouter:
    def test_list_tags(self, authenticated_client, create_tag):
        """Test listing tags with file counts."""
        tag = create_tag("List Tag", color="#123456")
        response = authenticated_client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()
        assert any(t["name"] == "List Tag" for t in data)
        # Check if file_count is present
        tag_data = next(t for t in data if t["name"] == "List Tag")
        assert "file_count" in tag_data

    def test_create_tag_success(self, authenticated_client, db_session):
        """Test creating a new tag."""
        payload = {"name": "New API Tag", "color": "#00FF00", "description": "Desc"}
        response = authenticated_client.post("/api/v1/tags", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "New API Tag"
        assert response.json()["color"] == "#00FF00"

    def test_create_tag_duplicate(self, authenticated_client, create_tag):
        """Test creating a duplicate tag name."""
        create_tag("Duplicate Tag")
        payload = {"name": "Duplicate Tag"}
        response = authenticated_client.post("/api/v1/tags", json=payload)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()

    def test_get_tag(self, authenticated_client, create_tag):
        """Test getting a single tag."""
        tag = create_tag("Get Tag")
        response = authenticated_client.get(f"/api/v1/tags/{tag.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Tag"

    def test_update_tag(self, authenticated_client, create_tag):
        """Test updating a tag."""
        tag = create_tag("Old Name")
        payload = {"name": "New Name", "color": "#FFFFFF"}
        response = authenticated_client.patch(f"/api/v1/tags/{tag.id}", json=payload)
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"
        assert response.json()["color"] == "#FFFFFF"

    def test_delete_tag(self, authenticated_client, db_session, create_tag):
        """Test deleting a tag."""
        tag = create_tag("To Delete")
        response = authenticated_client.delete(f"/api/v1/tags/{tag.id}")
        assert response.status_code == 204
        assert db_session.get(Tag, tag.id) is None

    def test_add_tag_to_file(self, authenticated_client, file_inventory_factory, create_tag):
        """Test adding a tag to a specific file."""
        inv = file_inventory_factory(path="/tmp/tagged_file.txt")
        file_id = inv.id
        tag = create_tag("File Tag")
        tag_id = tag.id
        
        payload = {"tag_id": tag_id, "tagged_by": "test_user"}
        response = authenticated_client.post(f"/api/v1/tags/files/{file_id}/tags", json=payload)
        assert response.status_code == 201
        assert response.json()["tag"]["id"] == tag_id
        assert response.json()["file_id"] == file_id

    def test_remove_tag_from_file(self, authenticated_client, db_session, file_inventory_factory, create_tag):
        """Test removing a tag from a file."""
        inv = file_inventory_factory(path="/tmp/untagged_file.txt")
        file_id = inv.id
        tag = create_tag("Remove Tag")
        tag_id = tag.id
        file_tag = FileTag(file_id=file_id, tag_id=tag_id)
        db_session.add(file_tag)
        db_session.commit()
        
        response = authenticated_client.delete(f"/api/v1/tags/files/{file_id}/tags/{tag_id}")
        assert response.status_code == 204
        # Verify gone
        exists = db_session.query(FileTag).filter_by(file_id=file_id, tag_id=tag_id).first()
        assert exists is None

    def test_bulk_add_tags(self, authenticated_client, file_inventory_factory, create_tag):
        """Test bulk adding a tag to multiple files."""
        inv1 = file_inventory_factory(path="/tmp/bulk1.txt")
        inv2 = file_inventory_factory(path="/tmp/bulk2.txt", path_name="other_p")
        tag = create_tag("Bulk Add Tag")
        
        payload = {"tag_id": tag.id, "file_ids": [inv1.id, inv2.id]}
        response = authenticated_client.post("/api/v1/tags/bulk/add", json=payload)
        assert response.status_code == 200
        assert response.json()["successful"] == 2
        assert response.json()["total"] == 2

    def test_bulk_remove_tags(self, authenticated_client, db_session, file_inventory_factory, create_tag):
        """Test bulk removing a tag from multiple files."""
        inv1 = file_inventory_factory(path="/tmp/bulk_rem1.txt")
        inv2 = file_inventory_factory(path="/tmp/bulk_rem2.txt", path_name="other_p2")
        tag = create_tag("Bulk Rem Tag")
        
        # Add tags first
        db_session.add(FileTag(file_id=inv1.id, tag_id=tag.id))
        db_session.add(FileTag(file_id=inv2.id, tag_id=tag.id))
        db_session.commit()
        
        payload = {"tag_id": tag.id, "file_ids": [inv1.id, inv2.id]}
        response = authenticated_client.post("/api/v1/tags/bulk/remove", json=payload)
        assert response.status_code == 200
        assert response.json()["successful"] == 2
        
        assert db_session.query(FileTag).filter_by(tag_id=tag.id).count() == 0

    def test_add_tag_to_file_not_found(self, authenticated_client, create_tag):
        """Test adding tag to non-existent file."""
        tag = create_tag("T1")
        payload = {"tag_id": tag.id}
        response = authenticated_client.post("/api/v1/tags/files/9999/tags", json=payload)
        assert response.status_code == 404

    def test_add_tag_not_found_to_file(self, authenticated_client, file_inventory_factory):
        """Test adding non-existent tag to a file."""
        inv = file_inventory_factory()
        payload = {"tag_id": 9999}
        response = authenticated_client.post(f"/api/v1/tags/files/{inv.id}/tags", json=payload)
        assert response.status_code == 404

    def test_remove_tag_from_file_not_found(self, authenticated_client, file_inventory_factory, create_tag):
        """Test removing tag from file when association doesn't exist."""
        inv = file_inventory_factory()
        tag = create_tag("T2")
        response = authenticated_client.delete(f"/api/v1/tags/files/{inv.id}/tags/{tag.id}")
        assert response.status_code == 404

    def test_get_file_tags_not_found(self, authenticated_client):
        """Test getting tags for non-existent file."""
        response = authenticated_client.get("/api/v1/tags/files/9999/tags")
        assert response.status_code == 404

    def test_bulk_add_tag_not_found(self, authenticated_client):
        """Test bulk adding a non-existent tag."""
        payload = {"tag_id": 9999, "file_ids": [1, 2]}
        response = authenticated_client.post("/api/v1/tags/bulk/add", json=payload)
        assert response.status_code == 200 # Endpoint returns 200 even if tag not found, with results
        assert response.json()["failed"] == 2
        assert "not found" in response.json()["results"][0]["message"].lower()
