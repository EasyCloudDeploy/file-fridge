"""Tests for notification system."""

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Notifier,
    Notification,
    NotificationDispatch,
    NotificationLevel,
    NotifierType,
)
from app.schemas import NotifierCreate, NotifierUpdate
from app.services.notification_service import notification_service
from app.services.notification_events import NotificationEventType, ScanCompletedData


@pytest.fixture
def db_session():
    """Create a test database session."""
    from app.database import SessionLocal, init_db
    init_db()

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestNotifierEncryption:
    """Test SMTP password encryption/decryption."""

    def test_smtp_password_encryption(self, db_session: Session):
        """Test that SMTP passwords are encrypted and can be decrypted."""
        from app.models import encryption_manager

        notifier = Notifier(
            name="Test Email",
            type=NotifierType.EMAIL,
            address="test@example.com",
            enabled=True,
            subscribed_events=["SCAN_COMPLETED"],
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="testuser",
        )

        # Set password via property (should encrypt)
        test_password = "my_secret_password"
        notifier.smtp_password = test_password

        db_session.add(notifier)
        db_session.commit()
        db_session.refresh(notifier)

        # Check encrypted value is stored
        assert notifier.smtp_password_encrypted != test_password
        assert "gAAAA" in notifier.smtp_password_encrypted

        # Check decrypted value matches original
        decrypted = notifier.smtp_password
        assert decrypted == test_password

        db_session.delete(notifier)
        db_session.commit()


class TestRateLimiter:
    """Test notification rate limiting."""

    def test_rate_limiting(self, db_session: Session):
        """Test that rate limiting prevents duplicate notifications."""
        from app.services.notification_service import rate_limiter

        event_type = NotificationEventType.DISK_SPACE_CRITICAL

        # First notification should be allowed
        assert rate_limiter.should_notify(1, event_type) is True

        # Simulate notification sent (set to current time)
        from datetime import datetime, timezone
        rate_limiter.last_notification[f"1:{event_type.value}"] = datetime.now(timezone.utc)

        # Immediate second notification should be rate limited
        assert rate_limiter.should_notify(1, event_type) is False

        # After cooldown, should be allowed again
        rate_limiter.reset(1, event_type)
        assert rate_limiter.should_notify(1, event_type) is True


class TestNotificationDispatch:
    """Test notification dispatch logic."""

    @pytest.mark.asyncio
    async def test_dispatch_event(self, db_session: Session):
        """Test event dispatch creates notification and dispatches."""
        event_data = ScanCompletedData(
            path_id=1,
            path_name="Test Path",
            files_moved=5,
            bytes_saved=1024000,
            scan_duration_seconds=10.5,
            errors=0,
        )

        # Mock notifier
        notifier = Notifier(
            name="Test Webhook",
            type=NotifierType.GENERIC_WEBHOOK,
            address="http://example.com/webhook",
            enabled=True,
            subscribed_events=[NotificationEventType.SCAN_COMPLETED.value],
        )
        db_session.add(notifier)
        db_session.commit()

        # Dispatch event
        notification = await notification_service.dispatch_event(
            db=db_session,
            event_type=NotificationEventType.SCAN_COMPLETED,
            event_data=event_data,
        )

        # Verify notification was created
        assert notification.id is not None
        assert notification.level == NotificationLevel.INFO
        assert "Scan completed" in notification.message

        # Verify dispatch was created
        dispatches = (
            db_session.query(NotificationDispatch)
            .filter(NotificationDispatch.notification_id == notification.id)
            .all()
        )
        assert len(dispatches) == 1
        assert dispatches[0].notifier_id == notifier.id

        # Cleanup
        db_session.delete(notifier)
        db_session.commit()


class TestNotificationValidation:
    """Test notification schema validation."""

    def test_email_notifier_requires_smtp_host(self):
        """Test that email notifiers require SMTP host."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="smtp_host is required"):
            NotifierCreate(
                name="Test",
                type=NotifierType.EMAIL,
                address="test@example.com",
                smtp_host=None,  # Missing required field
                smtp_sender="noreply@example.com",
                subscribed_events=["SCAN_COMPLETED"],
            )

    def test_email_notifier_requires_smtp_sender(self):
        """Test that email notifiers require SMTP sender."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="smtp_sender is required"):
            NotifierCreate(
                name="Test",
                type=NotifierType.EMAIL,
                address="test@example.com",
                smtp_host="smtp.example.com",
                smtp_sender=None,  # Missing required field
                subscribed_events=["SCAN_COMPLETED"],
            )

    def test_webhook_requires_https(self):
        """Test that webhook URLs must use HTTPS."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Webhook URLs must use HTTPS"):
            NotifierCreate(
                name="Test",
                type=NotifierType.GENERIC_WEBHOOK,
                address="http://example.com/webhook",  # HTTP, not HTTPS
                subscribed_events=["SCAN_COMPLETED"],
            )

    def test_webhook_with_https_valid(self):
        """Test that webhook with HTTPS is valid."""
        notifier = NotifierCreate(
            name="Test",
            type=NotifierType.GENERIC_WEBHOOK,
            address="https://example.com/webhook",  # HTTPS, should be valid
            subscribed_events=["SCAN_COMPLETED"],
        )
        assert notifier.type == NotifierType.GENERIC_WEBHOOK

    def test_valid_event_types(self):
        """Test that valid event types pass validation."""
        from app.services.notification_events import NotificationEventType

        notifier = NotifierCreate(
            name="Test",
            type=NotifierType.EMAIL,
            address="test@example.com",
            subscribed_events=[
                "SCAN_COMPLETED",
                "SCAN_ERROR",
                "PATH_CREATED",
                "PATH_UPDATED",
                "PATH_DELETED",
                "DISK_SPACE_CAUTION",
                "DISK_SPACE_CRITICAL",
            ],
        )
        assert len(notifier.subscribed_events) == 7

    def test_invalid_event_types(self):
        """Test that invalid event types fail validation."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid event types"):
            NotifierCreate(
                name="Test",
                type=NotifierType.EMAIL,
                address="test@example.com",
                subscribed_events=["SCAN_COMPLETED", "INVALID_EVENT"],
            )


class TestXSSPrevention:
    """Test XSS prevention in notification HTML."""

    def test_html_message_escaping(self, db_session: Session):
        """Test that HTML messages escape user-provided content."""
        from app.services.notification_service import NotificationService

        service = NotificationService()

        # Test with potentially malicious content
        malicious_message = '<script>alert("xss")</script> File moved'
        malicious_metadata = {"file_path": '<img src=x onerror=alert("xss")>'}

        html = service._format_html_message("INFO", malicious_message, malicious_metadata)

        # Verify script tags are escaped
        assert "<script>" not in html
        assert "&lt;script&gt;" in html or "script" not in html
        # Verify attributes are escaped (meaning the tag <img is not there)
        assert "<img" not in html
        assert "&lt;img" in html
