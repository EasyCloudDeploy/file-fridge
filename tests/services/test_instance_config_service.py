"""Tests for instance configuration service."""

import pytest

from app.models import InstanceMetadata
from app.services.instance_config_service import InstanceConfigService


def _ensure_metadata(db_session) -> InstanceMetadata:
    meta = db_session.query(InstanceMetadata).first()
    if meta is None:
        meta = InstanceMetadata(instance_uuid="test-instance-uuid")
        db_session.add(meta)
        db_session.commit()
        db_session.refresh(meta)
    return meta


@pytest.mark.unit
class TestInstanceConfigService:
    def test_get_instance_url_from_env(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "app.services.instance_config_service.settings.ff_instance_url", "https://env.example"
        )
        service = InstanceConfigService()
        assert service.get_instance_url(db_session) == "https://env.example"

    def test_get_instance_url_from_database(self, db_session, monkeypatch):
        monkeypatch.setattr("app.services.instance_config_service.settings.ff_instance_url", None)
        meta = _ensure_metadata(db_session)
        meta.instance_url = "https://db.example"
        db_session.commit()
        service = InstanceConfigService()
        assert service.get_instance_url(db_session) == "https://db.example"

    def test_get_instance_name_from_database(self, db_session, monkeypatch):
        monkeypatch.setattr("app.services.instance_config_service.settings.instance_name", None)
        meta = _ensure_metadata(db_session)
        meta.instance_name = "Fridge Node"
        db_session.commit()
        service = InstanceConfigService()
        assert service.get_instance_name(db_session) == "Fridge Node"

    def test_set_instance_url_creates_metadata_when_missing(self, db_session, monkeypatch):
        monkeypatch.setattr("app.services.instance_config_service.settings.ff_instance_url", None)
        db_session.query(InstanceMetadata).delete()
        db_session.commit()
        service = InstanceConfigService()
        updated = service.set_instance_url(db_session, "https://new.example")
        assert updated.instance_url == "https://new.example"

    def test_set_instance_name_updates_database(self, db_session, monkeypatch):
        monkeypatch.setattr("app.services.instance_config_service.settings.instance_name", None)
        service = InstanceConfigService()
        updated = service.set_instance_name(db_session, "Updated Name")
        assert updated.instance_name == "Updated Name"

    def test_smtp_settings_from_database(self, db_session, monkeypatch):
        monkeypatch.setattr("app.services.instance_config_service.settings.smtp_host", None)
        meta = _ensure_metadata(db_session)
        meta.smtp_host = "smtp.example.com"
        meta.smtp_port = 2525
        meta.smtp_user = "mailer"
        meta.smtp_password = "secret"
        meta.smtp_sender = "noreply@example.com"
        db_session.commit()
        service = InstanceConfigService()
        assert service.get_smtp_host(db_session) == "smtp.example.com"
        assert service.get_smtp_port(db_session) == 2525
        assert service.get_smtp_user(db_session) == "mailer"
        assert service.get_smtp_password(db_session) == "secret"
        assert service.get_smtp_sender(db_session) == "noreply@example.com"
