"""Notification service for creating and dispatching notifications."""
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

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
    LowDiskSpaceData,
    NotificationEvent,
    SyncErrorData,
    SyncSuccessData,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing and dispatching notifications."""

    WEBHOOK_TIMEOUT = 30

    async def dispatch_event(
        self,
        db: Session,
        event_type: NotificationEvent,
        data: Any,
        background_tasks: Optional[BackgroundTasks] = None,
    ):
        """
        Dispatch a structured notification event.

        This is the preferred method for sending notifications.
        """
        level, message, metadata = self._format_event(event_type, data)

        await self.create_and_dispatch_notification(
            db=db,
            level=level,
            message=message,
            metadata=metadata,
            background_tasks=background_tasks,
        )

    def _format_event(self, event_type: NotificationEvent, data: Any) -> tuple[str, str, Dict[str, Any]]:
        """Format a notification event into a message and metadata."""
        if event_type == NotificationEvent.SYNC_SUCCESS:
            data: SyncSuccessData
            level = "INFO"
            message = f"Successfully completed sync for path: {data.path_name}"
            metadata = data.dict()
        elif event_type == NotificationEvent.SYNC_ERROR:
            data: SyncErrorData
            level = "ERROR"
            message = f"Error during sync for path: {data.path_name}"
            metadata = data.dict()
        elif event_type == NotificationEvent.LOW_DISK_SPACE:
            data: LowDiskSpaceData
            level = "WARNING"
            message = f"Low disk space detected for storage location: {data.storage_name}"
            metadata = data.dict()
        else:
            level = "ERROR"
            message = f"Unknown notification event type: {event_type}"
            metadata = {"event_type": event_type, "data": str(data)}

        return level, message, metadata


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
            raise ValueError(f"Invalid notification level: {level}. Must be INFO, WARNING, or ERROR.")

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

    def _get_notifiers_for_level(self, db: Session, level: NotificationLevel) -> List[Notifier]:
        """Get all enabled notifiers that should receive notifications of this level."""
        level_hierarchy = {
            NotificationLevel.INFO: 0,
            NotificationLevel.WARNING: 1,
            NotificationLevel.ERROR: 2,
        }
        level_value = level_hierarchy[level]

        notifiers = db.query(Notifier).filter(Notifier.enabled == True).all()

        return [
            n for n in notifiers
            if level_value >= level_hierarchy[n.filter_level]
        ]

    async def _dispatch_to_notifiers(
        self,
        db: Session,
        notification: Notification,
        notifiers: List[Notifier],
        metadata: Optional[Dict[str, Any]] = None
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Dispatch a notification to a single notifier."""
        logger.info(f"Dispatching to notifier '{notifier.name}' ({notifier.type}) at {notifier.address}")

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
                    }
                )
            elif notifier.type == NotifierType.GENERIC_WEBHOOK:
                success, details = await self._send_webhook(
                    url=notifier.address,
                    level=notification.level.value,
                    message=notification.message,
                    metadata=metadata
                )
            else:
                success, details = False, f"Unknown notifier type: {notifier.type}"

            status = DispatchStatus.SUCCESS if success else DispatchStatus.FAILED
            self._log_dispatch(db, notification, notifier, status, details)

        except Exception as e:
            logger.exception(f"Unexpected error dispatching to notifier '{notifier.name}': {e}")
            self._log_dispatch(db, notification, notifier, DispatchStatus.FAILED, f"Unexpected error: {e!s}")

    async def _send_email(
        self,
        address: str,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        smtp_config: Optional[Dict[str, Any]] = None
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

        try:
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

        except aiosmtplib.SMTPException as e:
            return False, f"SMTP error: {e!s}"
        except Exception as e:
            return False, f"Error sending email: {e!s}"

    async def _send_webhook(
        self,
        url: str,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Send a webhook notification."""
        try:
            payload = {
                "level": level.upper(),
                "message": message,
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

        except httpx.TimeoutException:
            return False, f"Webhook timed out after {self.WEBHOOK_TIMEOUT}s"
        except httpx.RequestError as e:
            return False, f"Webhook request failed: {e!s}"
        except Exception as e:
            return False, f"Error sending webhook: {e!s}"

    def _log_dispatch(
        self,
        db: Session,
        notification: Notification,
        notifier: Notifier,
        status: DispatchStatus,
        details: str
    ) -> None:
        """Log a dispatch attempt to the database."""
        dispatch_log = NotificationDispatch(
            notification_id=notification.id,
            notifier_id=notifier.id,
            status=status,
            details=details
        )
        db.add(dispatch_log)
        db.commit()
        logger.info(f"Dispatch: notification #{notification.id} to '{notifier.name}' - {status.value}")

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
            "test_timestamp": datetime.utcnow().isoformat(),
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
                    }
                )
            if notifier.type == NotifierType.GENERIC_WEBHOOK:
                return await self._send_webhook(
                    url=notifier.address,
                    level="INFO",
                    message=test_message,
                    metadata=test_metadata
                )
            return False, f"Unknown notifier type: {notifier.type}"

        except Exception as e:
            return False, f"Error testing notifier: {e!s}"

    @staticmethod
    def _format_text_message(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format a notification message as plain text."""
        formatted = f"[{level.upper()}] {message}"
        if metadata:
            formatted += "\n\nAdditional Information:\n"
            for key, value in metadata.items():
                formatted += f"- {key}: {value}\n"
        return formatted

    @staticmethod
    def _format_html_message(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> str:
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
                    <p><strong>Level:</strong> <span style="color: {color};">{level.upper()}</span></p>
                    <p><strong>Message:</strong> {message}</p>
        """

        if metadata:
            html += "<p><strong>Additional Information:</strong></p><ul>"
            for key, value in metadata.items():
                html += f"<li>{key}: {value}</li>"
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
