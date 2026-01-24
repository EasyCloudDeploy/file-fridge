"""Service for logging security-relevant events."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import SecurityAuditLog

logger = logging.getLogger(__name__)


class SecurityAuditService:
    """Track security events for compliance and incident response."""

    def log_connection_created(self, db: Session, fingerprint: str, url: str, initiated_by: str):
        """Log new remote connection establishment."""
        self._log(
            db,
            "CONNECTION_CREATED",
            f"Remote: {fingerprint}",
            initiated_by,
            {"fingerprint": fingerprint, "url": url},
        )

    def log_connection_trusted(self, db: Session, fingerprint: str, initiated_by: str):
        """Log manual trust decision."""
        self._log(db, "CONNECTION_TRUSTED", f"Fingerprint: {fingerprint}", initiated_by)

    def log_signature_verification_failed(self, db: Session, fingerprint: str, reason: str):
        """Log failed signature verification (potential attack)."""
        self._log(
            db,
            "SIGNATURE_FAILED",
            reason,
            "system",
            {"fingerprint": fingerprint, "severity": "HIGH"},
        )

    def log_replay_attack_detected(self, db: Session, fingerprint: str, nonce: str):
        """Log detected replay attack."""
        self._log(
            db,
            "REPLAY_DETECTED",
            f"Nonce: {nonce}",
            "system",
            {"fingerprint": fingerprint, "severity": "CRITICAL"},
        )

    def _log(
        self, db: Session, event_type: str, message: str, initiated_by: str, event_metadata: dict = None
    ):
        """Internal logging helper."""
        entry = SecurityAuditLog(
            event_type=event_type,
            message=message,
            initiated_by=initiated_by,
            event_metadata=event_metadata or {},
            timestamp=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        logger.info(f"Security audit: {event_type} - {message}")


security_audit_service = SecurityAuditService()
