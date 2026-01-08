"""Base dispatcher class for notification system."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseDispatcher(ABC):
    """Abstract base class for notification dispatchers."""

    @abstractmethod
    async def send(
        self,
        address: str,
        level: str,
        message: str,
        metadata: Dict[str, Any] = None,
        smtp_config: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """
        Send a notification to the specified address.

        Args:
            address: Destination address (email, webhook URL, etc.)
            level: Notification level (INFO, WARNING, ERROR)
            message: Notification message content
            metadata: Optional additional metadata to include
            smtp_config: Optional SMTP configuration (for email dispatchers)

        Returns:
            Tuple of (success: bool, details: str)
            - success: True if dispatch succeeded, False otherwise
            - details: Success message or error details
        """
        pass

    def _format_message(self, level: str, message: str, metadata: Dict[str, Any] = None) -> str:
        """
        Format a notification message with level and metadata.

        Args:
            level: Notification level
            message: Message content
            metadata: Optional metadata

        Returns:
            Formatted message string
        """
        formatted = f"[{level.upper()}] {message}"
        if metadata:
            formatted += f"\n\nAdditional Information:\n"
            for key, value in metadata.items():
                formatted += f"- {key}: {value}\n"
        return formatted
