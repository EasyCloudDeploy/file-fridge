"""Notification service for creating and dispatching notifications."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from html import escape as html_escape
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

import aiosmtplib
import httpx
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
    EventData,
    NotificationEventType,
    PathCreatedData,
    PathDeletedData,
    PathUpdatedData,
    ScanCompletedData,
    ScanErrorData,
)

logger = logging.getLogger(__name__)


def async_retry(max_retries: int = 3, delay_seconds: float = 1.0):
    """Decorator for async retry logic with exponential backoff."""

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (
                    aiosmtplib.SMTPException,
                    httpx.TimeoutException,
                    httpx.RequestError,
                ) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay_seconds * (2**attempt)
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {wait_time:.1f}s..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"All {max_retries} attempts failed. Last error: {e}")
            raise last_exception

        return wrapper

    return decorator


class RateLimiter:
    """Simple rate limiter for notification dispatch."""

    def __init__(self, cooldown_minutes: int = 240):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.last_notification: Dict[str, datetime] = {}
        self.lock = Lock()

    def should_notify(self, notifier_id: int, event_type: NotificationEventType) -> bool:
        """Check if notification should be sent based on rate limit."""
        key = f"{notifier_id}:{event_type.value}"

        with self.lock:
            last_time = self.last_notification.get(key)
            if last_time is None or datetime.utcnow() - last_time >= self.cooldown:
                self.last_notification[key] = datetime.utcnow()
                return True
            return False

    def reset(self, notifier_id: int, event_type: NotificationEventType):
        """Reset rate limit for specific notifier/event (for testing)."""
        key = f"{notifier_id}:{event_type.value}"
        with self.lock:
            if key in self.last_notification:
                del self.last_notification[key]


rate_limiter = RateLimiter(cooldown_minutes=240)  # 4 hour cooldown


class NotificationService:
    """Service for managing and dispatching notifications."""

    WEBHOOK_TIMEOUT = 30

    def dispatch_event_sync(
        self,
        db: Session,
        event_type: NotificationEventType,
        event_data: EventData,
    ):
        """
        Synchronous wrapper for dispatch_event().
        Dispatches notification in background without blocking.

        Use this from synchronous routers/endpoints.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No event loop in current thread (sync context)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Fire and forget - don't block router response
        loop.create_task(self.dispatch_event(db, event_type, event_data))

    async def dispatch_event(
        self,
        db: Session,
        event_type: NotificationEventType,
        event_data: EventData,
        background_tasks: Optional[BackgroundTasks] = None,
    ):
        """
        Dispatch a structured notification event to all subscribed notifiers.

        Args:
            db: Database session
            event_type: Type of event (SCAN_COMPLETED, PATH_CREATED, etc.)
            event_data: Pydantic model with event-specific data
            background_tasks: Optional FastAPI background tasks
        """
        # Get notifiers subscribed to this event type
        notifiers = self._get_notifiers_for_event(db, event_type)

        if not notifiers:
            logger.debug(f"No notifiers subscribed to event: {event_type.value}")
            return None

        # Format message based on event type
        message = self._format_event(event_type, event_data)

        # Get legacy level for Notification.level column (backward compatibility)
        level = self._get_legacy_level_for_event(event_type)

        # Create notification record
        notification_level = NotificationLevel(level.lower())
        notification = Notification(level=notification_level, message=message)
        db.add(notification)
        db.commit()
        db.refresh(notification)

        logger.info(f"Created notification #{notification.id} for event {event_type.value}")

        # Dispatch to all subscribed notifiers
        metadata = event_data.dict()  # Convert Pydantic model to dict for metadata
        metadata["event_type"] = event_type.value

        if background_tasks:
            background_tasks.add_task(
                self._dispatch_to_notifiers, db, notification, notifiers, metadata
            )
            logger.info(f"Scheduled background dispatch to {len(notifiers)} notifier(s)")
        else:
            await self._dispatch_to_notifiers(db, notification, notifiers, metadata)

        return notification

    def _format_event(self, event_type: NotificationEventType, event_data: EventData) -> str:
        """
        Format event data into human-readable message.

        Args:
            event_type: Type of notification event
            event_data: Pydantic model with event-specific data

        Returns:
            Formatted message string
        """
        if isinstance(event_data, ScanCompletedData):
            return (
                f"Scan completed for path '{event_data.path_name}'\n"
                f"Files moved: {event_data.files_moved}\n"
                f"Bytes saved: {event_data.bytes_saved:,}\n"
                f"Duration: {event_data.scan_duration_seconds:.2f}s\n"
                f"Errors: {event_data.errors}"
            )

        if isinstance(event_data, ScanErrorData):
            return (
                f"Scan FAILED for path '{event_data.path_name}'\n"
                f"Error: {event_data.error_message}\n"
                f"{event_data.error_details or ''}"
            )

        if isinstance(event_data, PathCreatedData):
            return (
                f"New monitored path created: '{event_data.path_name}'\n"
                f"Source: {event_data.source_path}\n"
                f"Operation: {event_data.operation_type}"
            )

        if isinstance(event_data, PathUpdatedData):
            changes_str = "\n".join(f"  - {k}: {v}" for k, v in event_data.changes.items())
            return f"Monitored path updated: '{event_data.path_name}'\nChanges:\n{changes_str}"

        if isinstance(event_data, PathDeletedData):
            return (
                f"Monitored path deleted: '{event_data.path_name}'\n"
                f"Source: {event_data.source_path}"
            )

        if isinstance(event_data, DiskSpaceCautionData):
            return (
                f"CAUTION: Low disk space on '{event_data.location_name}'\n"
                f"Path: {event_data.location_path}\n"
                f"Free: {event_data.free_percent:.1f}% "
                f"({event_data.free_bytes / (1024**3):.2f} GB)\n"
                f"Threshold: {event_data.threshold_percent}%"
            )

        if isinstance(event_data, DiskSpaceCriticalData):
            return (
                f"CRITICAL: Very low disk space on '{event_data.location_name}'\n"
                f"Path: {event_data.location_path}\n"
                f"Free: {event_data.free_percent:.1f}% "
                f"({event_data.free_bytes / (1024**3):.2f} GB)\n"
                f"Threshold: {event_data.threshold_percent}%\n"
                f"IMMEDIATE ACTION REQUIRED!"
            )

        return f"Event: {event_type.value}"

    def _get_legacy_level_for_event(self, event_type: NotificationEventType) -> str:
        """
        Map event type to legacy notification level (for Notification.level column).
        This maintains backward compatibility with existing notification queries.

        Args:
            event_type: Type of notification event

        Returns:
            Level string (INFO, WARNING, or ERROR)
        """
        level_mapping = {
            NotificationEventType.SCAN_COMPLETED: "INFO",
            NotificationEventType.PATH_CREATED: "INFO",
            NotificationEventType.PATH_UPDATED: "INFO",
            NotificationEventType.PATH_DELETED: "INFO",
            NotificationEventType.DISK_SPACE_CAUTION: "WARNING",
            NotificationEventType.SCAN_ERROR: "ERROR",
            NotificationEventType.DISK_SPACE_CRITICAL: "ERROR",
        }
        return level_mapping.get(event_type, "INFO")

    async def create_and_dispatch_notification(
        self,
        db: Session,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> Notification:
        """Create a notification and dispatch it to all enabled notifiers."""
        # Validate level
        try:
            notification_level = NotificationLevel(level.lower())
        except ValueError:
            msg = f"Invalid notification level: {level}. Must be INFO, WARNING, or ERROR."
            raise ValueError(msg)

        # Create notification record
        notification = Notification(level=notification_level, message=message)
        db.add(notification)
        db.commit()
        db.refresh(notification)

        logger.info(f"Created notification #{notification.id} with level {level}")

        # Get enabled notifiers that should receive this level
        notifiers = self._get_notifiers_for_level(db, notification_level)

        if not notifiers:
            logger.info(f"No enabled notifiers found for level {level}")
            return notification

        # Dispatch to notifiers
        if background_tasks:
            background_tasks.add_task(
                self._dispatch_to_notifiers, db, notification, notifiers, metadata
            )
            logger.info(f"Scheduled background dispatch to {len(notifiers)} notifier(s)")
        else:
            await self._dispatch_to_notifiers(db, notification, notifiers, metadata)

        return notification

    def _get_notifiers_for_event(
        self, db: Session, event_type: NotificationEventType
    ) -> List[Notifier]:
        """
        Get all enabled notifiers subscribed to this event type.

        Uses JSON containment query to check if event_type is in subscribed_events array.

        Args:
            db: Database session
            event_type: Type of notification event

        Returns:
            List of enabled notifiers subscribed to this event
        """
        # For SQLite, we need to do JSON parsing manually
        # Get all enabled notifiers and filter in Python
        all_notifiers = db.query(Notifier).filter(Notifier.enabled == True).all()

        subscribed_notifiers = []
        for notifier in all_notifiers:
            # subscribed_events is a Python list (thanks to SQLAlchemy JSON type)
            if notifier.subscribed_events and event_type.value in notifier.subscribed_events:
                subscribed_notifiers.append(notifier)

        return subscribed_notifiers

    async def _dispatch_to_notifiers(
        self,
        db: Session,
        notification: Notification,
        notifiers: List[Notifier],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Dispatch a notification to multiple notifiers."""
        logger.info(f"Dispatching notification #{notification.id} to {len(notifiers)} notifier(s)")

        for notifier in notifiers:
            await self._dispatch_to_single_notifier(db, notification, notifier, metadata)

    async def _dispatch_to_single_notifier(
        self,
        db: Session,
        notification: Notification,
        notifier: Notifier,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Dispatch a notification to a single notifier."""
        logger.info(
            f"Dispatching to notifier '{notifier.name}' ({notifier.type}) at {notifier.address}"
        )

        # Apply rate limiting for event-based notifications
        # Only rate limit if we have metadata (indicates event-based dispatch)
        if metadata:
            event_type = metadata.get("event_type")
            if event_type:
                try:
                    event_enum = NotificationEventType(event_type)
                    if not rate_limiter.should_notify(notifier.id, event_enum):
                        logger.info(
                            f"Rate limited: Skipping notification to '{notifier.name}' for event {event_type.value}"
                        )
                        self._log_dispatch(
                            db,
                            notification,
                            notifier,
                            DispatchStatus.FAILED,
                            "Rate limited: Too many notifications",
                        )
                        return
                except ValueError:
                    pass  # Invalid event type, skip rate limiting

        try:
            if notifier.type == NotifierType.EMAIL:
                success, details = await self._send_email(
                    address=notifier.address,
                    level=notification.level.value,
                    message=notification.message,
                    metadata=metadata,
                    smtp_config={
                        "smtp_host": notifier.smtp_host,
                        "smtp_port": notifier.smtp_port,
                        "smtp_user": notifier.smtp_user,
                        "smtp_password": notifier.smtp_password,
                        "smtp_sender": notifier.smtp_sender,
                        "smtp_use_tls": notifier.smtp_use_tls,
                    },
                )
            elif notifier.type == NotifierType.GENERIC_WEBHOOK:
                success, details = await self._send_webhook(
                    url=notifier.address,
                    level=notification.level.value,
                    message=notification.message,
                    metadata=metadata,
                )
            else:
                success, details = False, f"Unknown notifier type: {notifier.type}"

            status = DispatchStatus.SUCCESS if success else DispatchStatus.FAILED
            self._log_dispatch(db, notification, notifier, status, details)

        except Exception as e:
            logger.exception(f"Unexpected error dispatching to notifier '{notifier.name}'")
            self._log_dispatch(
                db, notification, notifier, DispatchStatus.FAILED, f"Unexpected error: {e!s}"
            )

    @async_retry(max_retries=3, delay_seconds=1.0)
    async def _send_email(
        self,
        address: str,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        smtp_config: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Send an email notification."""
        if not smtp_config:
            return False, "SMTP configuration is required for email notifications"

        smtp_host = smtp_config.get("smtp_host")
        smtp_port = smtp_config.get("smtp_port", 587)
        smtp_user = smtp_config.get("smtp_user")
        smtp_password = smtp_config.get("smtp_password")
        smtp_sender = smtp_config.get("smtp_sender")
        smtp_use_tls = smtp_config.get("smtp_use_tls", True)

        if not smtp_host:
            return False, "SMTP host is not configured"
        if not smtp_sender:
            return False, "SMTP sender is not configured"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"File Fridge Notification - {level.upper()}"
        msg["From"] = smtp_sender
        msg["To"] = address

        # Plain text
        body = self._format_text_message(level, message, metadata)
        msg.attach(MIMEText(body, "plain"))

        # HTML
        html_body = self._format_html_message(level, message, metadata)
        msg.attach(MIMEText(html_body, "html"))

        logger.info(f"Sending email to {address} via {smtp_host}")

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            use_tls=smtp_use_tls,
        )

        return True, f"Email sent successfully to {address}"

    @async_retry(max_retries=3, delay_seconds=1.0)
    async def _send_webhook(
        self, url: str, level: str, message: str, metadata: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Send a webhook notification."""
        # Include multiple common field names for compatibility with different providers:
        # - "content" for Discord
        # - "text" for Slack
        # - "message" for generic webhooks
        formatted_message = f"[{level.upper()}] {message}"

        payload = {
            "content": formatted_message,  # Discord
            "text": formatted_message,     # Slack
            "message": message,            # Generic
            "level": level.upper(),
            "timestamp": datetime.utcnow().isoformat(),
            "source": "File Fridge",
        }
        if metadata:
            payload["metadata"] = metadata

        logger.info(f"Sending webhook to {url}")

        async with httpx.AsyncClient(timeout=self.WEBHOOK_TIMEOUT) as client:
            response = await client.post(url, json=payload)

            if 200 <= response.status_code < 300:
                return True, f"Webhook sent (Status: {response.status_code})"
            return False, f"Webhook returned status {response.status_code}"

    def _log_dispatch(
        self,
        db: Session,
        notification: Notification,
        notifier: Notifier,
        status: DispatchStatus,
        details: str,
    ) -> None:
        """Log a dispatch attempt to the database."""
        dispatch_log = NotificationDispatch(
            notification_id=notification.id, notifier_id=notifier.id, status=status, details=details
        )
        db.add(dispatch_log)
        db.commit()
        logger.info(
            f"Dispatch: notification #{notification.id} to '{notifier.name}' - {status.value}"
        )

    async def test_notifier(self, db: Session, notifier_id: int) -> tuple:
        """Send a test notification to a specific notifier."""
        notifier = db.query(Notifier).filter(Notifier.id == notifier_id).first()
        if not notifier:
            return False, f"Notifier with ID {notifier_id} not found"

        logger.info(f"Testing notifier '{notifier.name}' ({notifier.type})")

        test_message = "This is a test notification from File Fridge."
        test_metadata = {
            "notifier_name": notifier.name,
            "notifier_type": notifier.type.value,
            "test_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

        try:
            if notifier.type == NotifierType.EMAIL:
                return await self._send_email(
                    address=notifier.address,
                    level="INFO",
                    message=test_message,
                    metadata=test_metadata,
                    smtp_config={
                        "smtp_host": notifier.smtp_host,
                        "smtp_port": notifier.smtp_port,
                        "smtp_user": notifier.smtp_user,
                        "smtp_password": notifier.smtp_password,
                        "smtp_sender": notifier.smtp_sender,
                        "smtp_use_tls": notifier.smtp_use_tls,
                    },
                )
            if notifier.type == NotifierType.GENERIC_WEBHOOK:
                return await self._send_webhook(
                    url=notifier.address, level="INFO", message=test_message, metadata=test_metadata
                )
            return False, f"Unknown notifier type: {notifier.type}"

        except Exception as e:
            return False, f"Error testing notifier: {e!s}"

    @staticmethod
    def _format_text_message(
        level: str, message: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Format a notification message as plain text."""
        formatted = f"[{level.upper()}] {message}"
        if metadata:
            formatted += "\n\nAdditional Information:\n"
            for key, value in metadata.items():
                formatted += f"- {key}: {value}\n"
        return formatted

    @staticmethod
    def _format_html_message(
        level: str, message: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Format a notification message as HTML."""
        color_map = {"INFO": "#2196F3", "WARNING": "#FF9800", "ERROR": "#F44336"}
        color = color_map.get(level.upper(), "#666666")

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background-color: {color}; color: white; padding: 10px 20px; border-radius: 5px 5px 0 0;">
                    <h2 style="margin: 0;">File Fridge Notification</h2>
                </div>
                <div style="background-color: #f5f5f5; padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 5px 5px;">
                    <p><strong>Level:</strong> <span style="color: {color};">{html_escape(level.upper())}</span></p>
                    <p><strong>Message:</strong> {html_escape(message)}</p>
        """

        if metadata:
            html += "<p><strong>Additional Information:</strong></p><ul>"
            for key, value in metadata.items():
                html += f"<li>{html_escape(str(key))}: {html_escape(str(value))}</li>"
            html += "</ul>"

        html += """
                    <p style="font-size: 12px; color: #999; margin-top: 20px;">
                        Automated notification from File Fridge
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        return html


# Global service instance
notification_service = NotificationService()
