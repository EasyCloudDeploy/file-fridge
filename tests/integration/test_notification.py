
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
import aiosmtplib
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Notifier, NotifierType, Notification, NotificationDispatch, NotificationLevel, DispatchStatus
from app.services.notification_events import (
    NotificationEventType,
    ScanCompletedData,
    PathCreatedData,
    DiskSpaceCriticalData,
    ScanErrorData,
)
from app.services.notification_service import NotificationService, rate_limiter

# Reset rate limiter for each test
@pytest.fixture(autouse=True)
def reset_rate_limiter():
    rate_limiter.last_notification = {}
    yield


@pytest.fixture
def email_notifier(db_session: Session):
    """Fixture for an email notifier."""
    notifier = Notifier(
        name="Test Email Notifier",
        type=NotifierType.EMAIL,
        address="recipient@example.com",
        enabled=True,
        subscribed_events=[NotificationEventType.SCAN_COMPLETED.value],
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="password",
        smtp_sender="sender@example.com",
    )
    db_session.add(notifier)
    db_session.commit()
    db_session.refresh(notifier)
    return notifier


@pytest.fixture
def webhook_notifier(db_session: Session):
    """Fixture for a webhook notifier."""
    notifier = Notifier(
        name="Test Webhook Notifier",
        type=NotifierType.GENERIC_WEBHOOK,
        address="http://webhook.site/test",
        enabled=True,
        subscribed_events=[NotificationEventType.DISK_SPACE_CRITICAL.value],
    )
    db_session.add(notifier)
    db_session.commit()
    db_session.refresh(notifier)
    return notifier


@patch("aiosmtplib.send", new_callable=AsyncMock)
async def test_dispatch_event_email_success(
    mock_send_email, email_notifier, db_session: Session
):
    """Test dispatching an event to an email notifier successfully."""
    notification_service = NotificationService()
    event_data = ScanCompletedData(
        path_id=1, path_name="test_path", files_moved=5, bytes_saved=1024, scan_duration_seconds=10.5
    )

    await notification_service.dispatch_event(db_session, NotificationEventType.SCAN_COMPLETED, event_data)

    mock_send_email.assert_called_once()
    assert db_session.query(Notification).count() == 1
    assert db_session.query(NotificationDispatch).count() == 1
    dispatch = db_session.query(NotificationDispatch).first()
    assert dispatch.status == DispatchStatus.SUCCESS


@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_dispatch_event_webhook_success(
    mock_httpx_post, webhook_notifier, db_session: Session
):
    """Test dispatching an event to a webhook notifier successfully."""
    notification_service = NotificationService()
    event_data = DiskSpaceCriticalData(
        location_id=1,
        location_name="cold_storage",
        location_path="/mnt/cold",
        free_percent=5.0,
        threshold_percent=10,
        free_bytes=5 * (1024**3),
        total_bytes=100 * (1024**3),
    )
    mock_response = MagicMock(status_code=200)
    mock_httpx_post.return_value = mock_response

    await notification_service.dispatch_event(db_session, NotificationEventType.DISK_SPACE_CRITICAL, event_data)

    mock_httpx_post.assert_called_once()
    assert db_session.query(Notification).count() == 1
    assert db_session.query(NotificationDispatch).count() == 1
    dispatch = db_session.query(NotificationDispatch).first()
    assert dispatch.status == DispatchStatus.SUCCESS


@patch("aiosmtplib.send", new_callable=AsyncMock, side_effect=aiosmtplib.SMTPException("SMTP Error"))
async def test_dispatch_event_email_failure(
    mock_send_email, email_notifier, db_session: Session
):
    """Test dispatching an event to an email notifier that fails."""
    notification_service = NotificationService()
    event_data = ScanCompletedData(
        path_id=1, path_name="test_path", files_moved=5, bytes_saved=1024, scan_duration_seconds=10.5
    )

    await notification_service.dispatch_event(db_session, NotificationEventType.SCAN_COMPLETED, event_data)

    # Should retry 3 times (default for async_retry)
    assert mock_send_email.call_count == 3
    assert db_session.query(Notification).count() == 1
    assert db_session.query(NotificationDispatch).count() == 1
    dispatch = db_session.query(NotificationDispatch).first()
    assert dispatch.status == DispatchStatus.FAILED
    assert "SMTP Error" in dispatch.details


async def test_dispatch_event_rate_limiting(email_notifier, db_session: Session):
    """Test that notifications are rate-limited."""
    notification_service = NotificationService()
    event_data = PathCreatedData(
        path_id=1, path_name="new_path", source_path="/path/to/new", operation_type="move"
    )
    
    # Subscribe email_notifier to PATH_CREATED for this test
    email_notifier.subscribed_events = list(email_notifier.subscribed_events) + [NotificationEventType.PATH_CREATED.value]
    db_session.commit()
    db_session.refresh(email_notifier)

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send_email:
        # First dispatch should go through
        await notification_service.dispatch_event(db_session, NotificationEventType.PATH_CREATED, event_data)
        assert mock_send_email.call_count == 1
        assert db_session.query(NotificationDispatch).count() == 1
        dispatch1 = db_session.query(NotificationDispatch).filter_by(notifier_id=email_notifier.id).first()
        assert dispatch1.status == DispatchStatus.SUCCESS

        # Second dispatch (within cooldown period) should be rate-limited
        await notification_service.dispatch_event(db_session, NotificationEventType.PATH_CREATED, event_data)

        assert mock_send_email.call_count == 1 # Still only one actual send attempt
        assert db_session.query(NotificationDispatch).count() == 2 # But a FAILED dispatch log is recorded
        dispatch2 = db_session.query(NotificationDispatch).filter(NotificationDispatch.id != dispatch1.id).first()
        assert dispatch2.status == DispatchStatus.FAILED
        assert "Rate limited" in dispatch2.details


@patch("aiosmtplib.send", new_callable=AsyncMock)
async def test_test_notifier_email_success(mock_send_email, email_notifier, db_session: Session):
    """Test the test_notifier method for email."""
    notification_service = NotificationService()
    success, details = await notification_service.test_notifier(db_session, email_notifier.id)

    assert success is True
    assert "Email sent successfully" in details
    mock_send_email.assert_called_once()
    # No Notification or NotificationDispatch should be created for test_notifier
    assert db_session.query(Notification).count() == 0
    assert db_session.query(NotificationDispatch).count() == 0


@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_test_notifier_webhook_success(
    mock_httpx_post, webhook_notifier, db_session: Session
):
    """Test the test_notifier method for webhook."""
    notification_service = NotificationService()
    mock_response = MagicMock(status_code=200)
    mock_httpx_post.return_value = mock_response

    success, details = await notification_service.test_notifier(db_session, webhook_notifier.id)

    assert success is True
    assert "Webhook sent" in details
    mock_httpx_post.assert_called_once()
    assert db_session.query(Notification).count() == 0
    assert db_session.query(NotificationDispatch).count() == 0
