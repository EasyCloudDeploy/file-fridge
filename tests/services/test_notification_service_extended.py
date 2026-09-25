"""Extended unit tests for notification_service coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.models import (
    DispatchStatus,
    Notification,
    NotificationDispatch,
    NotificationLevel,
    Notifier,
    NotifierType,
)
from app.services.notification_events import (
    DiskSpaceCautionData,
    DiskSpaceCriticalData,
    NotificationEventType,
    PathCreatedData,
    PathDeletedData,
    PathUpdatedData,
    ScanCompletedData,
    ScanErrorData,
    StorageLocationStats,
    StoragePermissionErrorData,
)
from app.services.notification_service import (
    NotificationService,
    RateLimiter,
    async_retry,
    rate_limiter,
)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    rate_limiter.last_notification = {}
    yield
    rate_limiter.last_notification = {}


@pytest.fixture
def service():
    return NotificationService()


@pytest.fixture
def email_notifier(db_session: Session):
    notifier = Notifier(
        name="Extended Email",
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


def test_format_event_scan_completed_full(service):
    event = ScanCompletedData(
        path_id=1,
        path_name="media",
        files_scanned=100,
        files_moved=5,
        files_skipped=2,
        bytes_moved=3 * 1024**3,
        scan_duration_seconds=125.5,
        cold_storages_updated=["Cold A"],
        hot_storage=StorageLocationStats(
            name="Hot",
            total_bytes=100 * 1024**3,
            free_bytes=50 * 1024**3,
            free_percent=50.0,
        ),
        cold_storages=[
            StorageLocationStats(
                name="Cold A",
                total_bytes=10 * 1024**3,
                free_bytes=8 * 1024**3,
                free_percent=80.0,
            )
        ],
        errors=2,
    )
    message = service._format_event(NotificationEventType.SCAN_COMPLETED, event)
    assert "media" in message
    assert "2m 5s" in message
    assert "Cold A" in message
    assert "Errors: 2" in message


def test_format_event_other_types(service):
    assert "Scan FAILED" in service._format_event(
        NotificationEventType.SCAN_ERROR,
        ScanErrorData(path_id=1, path_name="p", error_message="boom", error_details="detail"),
    )
    assert "created" in service._format_event(
        NotificationEventType.PATH_CREATED,
        PathCreatedData(path_id=1, path_name="p", source_path="/s", operation_type="move"),
    )
    assert "Changes:" in service._format_event(
        NotificationEventType.PATH_UPDATED,
        PathUpdatedData(path_id=1, path_name="p", changes={"enabled": "false -> true"}),
    )
    assert "deleted" in service._format_event(
        NotificationEventType.PATH_DELETED,
        PathDeletedData(path_id=1, path_name="p", source_path="/s"),
    )
    assert "CAUTION" in service._format_event(
        NotificationEventType.DISK_SPACE_CAUTION,
        DiskSpaceCautionData(
            location_id=1,
            location_name="cold",
            location_path="/cold",
            free_percent=12.0,
            threshold_percent=15,
            free_bytes=1024**3,
            total_bytes=10 * 1024**3,
        ),
    )
    assert "CRITICAL" in service._format_event(
        NotificationEventType.DISK_SPACE_CRITICAL,
        DiskSpaceCriticalData(
            location_id=1,
            location_name="cold",
            location_path="/cold",
            free_percent=2.0,
            threshold_percent=5,
            free_bytes=512 * 1024**2,
            total_bytes=10 * 1024**3,
        ),
    )
    assert "PERMISSION ERROR" in service._format_event(
        NotificationEventType.STORAGE_PERMISSION_ERROR,
        StoragePermissionErrorData(
            location_id=1,
            location_name="cold",
            location_path="/cold",
            storage_type="local",
            missing_permissions=["read", "write"],
        ),
    )


def test_rate_limiter_reset():
    limiter = RateLimiter(cooldown_minutes=240)
    assert limiter.should_notify(1, NotificationEventType.SCAN_COMPLETED) is True
    assert limiter.should_notify(1, NotificationEventType.SCAN_COMPLETED) is False
    limiter.reset(1, NotificationEventType.SCAN_COMPLETED)
    assert limiter.should_notify(1, NotificationEventType.SCAN_COMPLETED) is True


@pytest.mark.asyncio
async def test_dispatch_event_no_subscribers(db_session: Session, service):
    result = await service.dispatch_event(
        db_session,
        NotificationEventType.SCAN_COMPLETED,
        ScanCompletedData(
            path_id=1,
            path_name="empty",
            files_moved=0,
            bytes_moved=0,
            bytes_saved=0,
            scan_duration_seconds=1.0,
        ),
    )
    assert result is None
    assert db_session.query(Notification).count() == 0


@pytest.mark.asyncio
async def test_create_and_dispatch_invalid_level(db_session: Session, service):
    with pytest.raises(ValueError, match="Invalid notification level"):
        await service.create_and_dispatch_notification(db_session, "INVALID", "msg")


@pytest.mark.asyncio
async def test_create_and_dispatch_with_background_tasks(
    db_session: Session, service, email_notifier
):
    service._get_notifiers_for_level = lambda _db, _level: [email_notifier]  # type: ignore[method-assign]

    bg = BackgroundTasks()
    with patch("aiosmtplib.send", new_callable=AsyncMock):
        notification = await service.create_and_dispatch_notification(
            db_session, "INFO", "hello", background_tasks=bg
        )
    assert notification.id is not None
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_send_email_missing_config(service):
    ok, detail = await service._send_email("a@b.com", "INFO", "msg", smtp_config=None)
    assert ok is False
    assert "SMTP configuration" in detail


@pytest.mark.asyncio
async def test_send_email_missing_host(service):
    ok, detail = await service._send_email(
        "a@b.com",
        "INFO",
        "msg",
        smtp_config={"smtp_sender": "s@x.com"},
    )
    assert ok is False
    assert "host" in detail.lower()


@pytest.mark.asyncio
async def test_send_webhook_non_success_status(service):
    mock_response = MagicMock(status_code=500)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        ok, detail = await service._send_webhook("http://hook", "ERROR", "fail")
    assert ok is False
    assert "500" in detail


@pytest.mark.asyncio
async def test_dispatch_unknown_notifier_type(db_session: Session, service):
    notifier = MagicMock()
    notifier.name = "Unknown"
    notifier.type = "sms"
    notifier.address = "x"
    notifier.id = 99

    notification = Notification(level=NotificationLevel.INFO, message="test")
    db_session.add(notification)
    db_session.commit()

    await service._dispatch_to_single_notifier(db_session, notification, notifier, None)

    logs = db_session.query(NotificationDispatch).all()
    assert logs[-1].status == DispatchStatus.FAILED
    assert "Unknown notifier type" in logs[-1].details


def test_dispatch_event_sync_runs(db_session: Session, service):
    with patch.object(service, "dispatch_event", new_callable=AsyncMock) as mock_dispatch:
        service.dispatch_event_sync(
            db_session,
            NotificationEventType.SCAN_COMPLETED,
            ScanCompletedData(
                path_id=1,
                path_name="sync",
                files_moved=0,
                bytes_moved=0,
                scan_duration_seconds=0.5,
            ),
        )
    mock_dispatch.assert_awaited_once()


def test_dispatch_event_sync_swallows_errors(db_session: Session, service):
    with patch.object(
        service, "dispatch_event", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        service.dispatch_event_sync(
            db_session,
            NotificationEventType.SCAN_COMPLETED,
            ScanCompletedData(
                path_id=1,
                path_name="sync",
                files_moved=0,
                bytes_moved=0,
                scan_duration_seconds=0.5,
            ),
        )


@pytest.mark.asyncio
async def test_async_retry_exhausts_attempts():
    calls = {"n": 0}

    @async_retry(max_retries=2, delay_seconds=0.01)
    async def flaky():
        calls["n"] += 1
        raise httpx.RequestError("network")

    with pytest.raises(httpx.RequestError):
        await flaky()
    assert calls["n"] == 2


def test_format_text_and_html_messages(service):
    text = NotificationService._format_text_message("warning", "hello", {"k": "v"})
    assert "[WARNING]" in text
    assert "k: v" in text
    html = NotificationService._format_html_message("error", "bad <script>", {"x": "y"})
    assert "&lt;script&gt;" in html
    assert "ERROR" in html
