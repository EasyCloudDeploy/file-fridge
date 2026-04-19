import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import RemoteAuditLog

logger = logging.getLogger(__name__)


class RemoteAuditService:
    @staticmethod
    def log_event(
        db: Session,
        event_type: str,
        connection_id: Optional[int] = None,
        connection_name: Optional[str] = None,
        direction: Optional[str] = None,
        file_path: Optional[str] = None,
        file_size: Optional[int] = None,
        checksum: Optional[str] = None,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        """Log a remote transfer related event to the database."""
        try:
            audit_log = RemoteAuditLog(
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                connection_id=connection_id,
                connection_name=connection_name,
                direction=direction,
                file_path=file_path,
                file_size=file_size,
                checksum=checksum,
                status=status,
                error_message=error_message,
            )
            db.add(audit_log)
            db.commit()
            logger.debug(f"Logged remote audit event: {event_type} for connection {connection_id}")
        except Exception as e:
            logger.error(f"Failed to log remote audit event: {e}")
            db.rollback()


remote_audit_service = RemoteAuditService()
