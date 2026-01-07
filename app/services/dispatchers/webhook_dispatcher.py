"""Webhook dispatcher for notification system."""
from typing import Dict, Any
import logging
import httpx
from datetime import datetime

from .base_dispatcher import BaseDispatcher

logger = logging.getLogger(__name__)


class WebhookDispatcher(BaseDispatcher):
    """Dispatcher for sending notifications via generic webhooks."""

    def __init__(self, timeout: int = 30):
        """
        Initialize webhook dispatcher.

        Args:
            timeout: Request timeout in seconds (default: 30)
        """
        self.timeout = timeout

    async def send(
        self,
        address: str,
        level: str,
        message: str,
        metadata: Dict[str, Any] = None,
        smtp_config: Dict[str, Any] = None
    ) -> tuple[bool, str]:
        """
        Send a notification to a webhook URL.

        Args:
            address: Webhook URL
            level: Notification level (INFO, WARNING, ERROR)
            message: Notification message content
            metadata: Optional additional metadata

        Returns:
            Tuple of (success: bool, details: str)
        """
        try:
            # Prepare payload
            payload = {
                "level": level.upper(),
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "File Fridge",
            }

            # Add metadata if provided
            if metadata:
                payload["metadata"] = metadata

            logger.info(f"Sending webhook notification to {address}")

            # Send POST request
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    address,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                # Check response status
                if response.status_code >= 200 and response.status_code < 300:
                    success_msg = f"Webhook sent successfully to {address} (Status: {response.status_code})"
                    logger.info(success_msg)
                    return True, success_msg
                else:
                    error_msg = f"Webhook returned error status {response.status_code}: {response.text[:200]}"
                    logger.warning(f"Webhook to {address} failed: {error_msg}")
                    return False, error_msg

        except httpx.TimeoutException:
            error_msg = f"Webhook request to {address} timed out after {self.timeout} seconds"
            logger.error(error_msg)
            return False, error_msg

        except httpx.RequestError as e:
            error_msg = f"Webhook request to {address} failed: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Unexpected error sending webhook to {address}: {str(e)}"
            logger.exception(error_msg)
            return False, error_msg
