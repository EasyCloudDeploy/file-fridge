"""Notification service for creating and dispatching notifications."""
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks

from app.models import Notifier, Notification, NotificationDispatch, NotifierType, NotificationLevel, DispatchStatus
from app.services.dispatchers import EmailDispatcher, WebhookDispatcher

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing and dispatching notifications."""

    def __init__(self):
        """Initialize notification service with dispatchers."""
        self.email_dispatcher = EmailDispatcher()
        self.webhook_dispatcher = WebhookDispatcher()

    async def create_and_dispatch_notification(
        self,
        db: Session,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> Notification:
        """
        Create a notification and dispatch it to all enabled notifiers.

        Args:
            db: Database session
            level: Notification level (INFO, WARNING, ERROR)
            message: Notification message
            metadata: Optional metadata to include
            background_tasks: Optional FastAPI background tasks for async dispatch

        Returns:
            Created Notification object
        """
        # Validate level
        try:
            notification_level = NotificationLevel(level.lower())
        except ValueError:
            logger.error(f"Invalid notification level: {level}")
            raise ValueError(f"Invalid notification level: {level}. Must be INFO, WARNING, or ERROR.")

        # Create notification record
        notification = Notification(
            level=notification_level,
            message=message
        )
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
            # Dispatch in background
            background_tasks.add_task(
                self._dispatch_to_notifiers,
                db,
                notification,
                notifiers,
                metadata
            )
            logger.info(f"Scheduled background dispatch to {len(notifiers)} notifier(s)")
        else:
            # Dispatch synchronously
            await self._dispatch_to_notifiers(db, notification, notifiers, metadata)

        return notification

    def _get_notifiers_for_level(self, db: Session, level: NotificationLevel) -> list[Notifier]:
        """
        Get all enabled notifiers that should receive notifications of this level.

        Args:
            db: Database session
            level: Notification level

        Returns:
            List of enabled notifiers
        """
        # Define level hierarchy: INFO < WARNING < ERROR
        level_hierarchy = {
            NotificationLevel.INFO: 0,
            NotificationLevel.WARNING: 1,
            NotificationLevel.ERROR: 2,
        }

        # Get level value
        level_value = level_hierarchy[level]

        # Fetch notifiers
        notifiers = db.query(Notifier).filter(Notifier.enabled == True).all()

        # Filter based on filter_level
        filtered_notifiers = []
        for notifier in notifiers:
            notifier_filter_value = level_hierarchy[notifier.filter_level]
            # Send if notification level >= notifier's filter level
            if level_value >= notifier_filter_value:
                filtered_notifiers.append(notifier)

        return filtered_notifiers

    async def _dispatch_to_notifiers(
        self,
        db: Session,
        notification: Notification,
        notifiers: list[Notifier],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Dispatch a notification to multiple notifiers.

        Args:
            db: Database session
            notification: Notification to dispatch
            notifiers: List of notifiers to send to
            metadata: Optional metadata
        """
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
        """
        Dispatch a notification to a single notifier.

        Args:
            db: Database session
            notification: Notification to dispatch
            notifier: Notifier to send to
            metadata: Optional metadata
        """
        logger.info(f"Dispatching to notifier '{notifier.name}' ({notifier.type}) at {notifier.address}")

        # Select appropriate dispatcher
        if notifier.type == NotifierType.EMAIL:
            dispatcher = self.email_dispatcher
        elif notifier.type == NotifierType.GENERIC_WEBHOOK:
            dispatcher = self.webhook_dispatcher
        else:
            logger.error(f"Unknown notifier type: {notifier.type}")
            self._log_dispatch(
                db,
                notification,
                notifier,
                DispatchStatus.FAILED,
                f"Unknown notifier type: {notifier.type}"
            )
            return

        # Prepare SMTP config if this is an email notifier
        smtp_config = None
        if notifier.type == NotifierType.EMAIL:
            smtp_config = {
                'smtp_host': notifier.smtp_host,
                'smtp_port': notifier.smtp_port,
                'smtp_user': notifier.smtp_user,
                'smtp_password': notifier.smtp_password,
                'smtp_sender': notifier.smtp_sender,
                'smtp_use_tls': notifier.smtp_use_tls,
            }

        # Send notification
        try:
            success, details = await dispatcher.send(
                address=notifier.address,
                level=notification.level.value,
                message=notification.message,
                metadata=metadata,
                smtp_config=smtp_config
            )

            # Log result
            status = DispatchStatus.SUCCESS if success else DispatchStatus.FAILED
            self._log_dispatch(db, notification, notifier, status, details)

        except Exception as e:
            logger.exception(f"Unexpected error dispatching to notifier '{notifier.name}': {e}")
            self._log_dispatch(
                db,
                notification,
                notifier,
                DispatchStatus.FAILED,
                f"Unexpected error: {str(e)}"
            )

    def _log_dispatch(
        self,
        db: Session,
        notification: Notification,
        notifier: Notifier,
        status: DispatchStatus,
        details: str
    ) -> None:
        """
        Log a dispatch attempt to the database.

        Args:
            db: Database session
            notification: Notification that was dispatched
            notifier: Notifier it was sent to
            status: Dispatch status (SUCCESS or FAILED)
            details: Details or error message
        """
        dispatch_log = NotificationDispatch(
            notification_id=notification.id,
            notifier_id=notifier.id,
            status=status,
            details=details
        )
        db.add(dispatch_log)
        db.commit()

        logger.info(f"Logged dispatch: notification #{notification.id} to notifier '{notifier.name}' - {status.value}")

    async def test_notifier(self, db: Session, notifier_id: int) -> tuple[bool, str]:
        """
        Send a test notification to a specific notifier.

        Args:
            db: Database session
            notifier_id: ID of notifier to test

        Returns:
            Tuple of (success: bool, details: str)
        """
        # Fetch notifier
        notifier = db.query(Notifier).filter(Notifier.id == notifier_id).first()
        if not notifier:
            return False, f"Notifier with ID {notifier_id} not found"

        logger.info(f"Testing notifier '{notifier.name}' ({notifier.type})")

        # Select dispatcher
        if notifier.type == NotifierType.EMAIL:
            dispatcher = self.email_dispatcher
        elif notifier.type == NotifierType.GENERIC_WEBHOOK:
            dispatcher = self.webhook_dispatcher
        else:
            return False, f"Unknown notifier type: {notifier.type}"

        # Prepare SMTP config if this is an email notifier
        smtp_config = None
        if notifier.type == NotifierType.EMAIL:
            smtp_config = {
                'smtp_host': notifier.smtp_host,
                'smtp_port': notifier.smtp_port,
                'smtp_user': notifier.smtp_user,
                'smtp_password': notifier.smtp_password,
                'smtp_sender': notifier.smtp_sender,
                'smtp_use_tls': notifier.smtp_use_tls,
            }

        # Send test message
        test_message = "This is a test notification from File Fridge. If you received this, your notifier is configured correctly!"
        test_metadata = {
            "notifier_name": notifier.name,
            "notifier_type": notifier.type.value,
            "test_timestamp": str(logger),
        }

        try:
            success, details = await dispatcher.send(
                address=notifier.address,
                level="INFO",
                message=test_message,
                metadata=test_metadata,
                smtp_config=smtp_config
            )
            return success, details

        except Exception as e:
            error_msg = f"Error testing notifier: {str(e)}"
            logger.exception(error_msg)
            return False, error_msg


# Global service instance
notification_service = NotificationService()
