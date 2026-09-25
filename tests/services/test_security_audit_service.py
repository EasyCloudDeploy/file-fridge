"""Tests for security audit logging."""

import pytest

from app.models import SecurityAuditLog
from app.services.security_audit_service import security_audit_service


@pytest.mark.unit
class TestSecurityAuditService:
    def test_log_connection_created(self, db_session):
        security_audit_service.log_connection_created(
            db_session, "fp-abc", "https://peer.example", "admin"
        )
        db_session.commit()
        entry = db_session.query(SecurityAuditLog).one()
        assert entry.event_type == "CONNECTION_CREATED"
        assert entry.event_metadata["fingerprint"] == "fp-abc"

    def test_log_connection_trusted(self, db_session):
        security_audit_service.log_connection_trusted(db_session, "fp-trust", "user1")
        db_session.commit()
        entry = db_session.query(SecurityAuditLog).one()
        assert entry.event_type == "CONNECTION_TRUSTED"

    def test_log_signature_verification_failed(self, db_session):
        security_audit_service.log_signature_verification_failed(
            db_session, "fp-bad", "invalid signature"
        )
        db_session.commit()
        entry = db_session.query(SecurityAuditLog).one()
        assert entry.event_type == "SIGNATURE_FAILED"
        assert entry.event_metadata["severity"] == "HIGH"

    def test_log_replay_attack_detected(self, db_session):
        security_audit_service.log_replay_attack_detected(db_session, "fp-replay", "nonce-1")
        db_session.commit()
        entry = db_session.query(SecurityAuditLog).one()
        assert entry.event_type == "REPLAY_DETECTED"
        assert entry.event_metadata["severity"] == "CRITICAL"
