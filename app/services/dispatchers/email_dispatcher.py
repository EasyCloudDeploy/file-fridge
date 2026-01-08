"""Email dispatcher for notification system."""
from typing import Dict, Any, Optional
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiosmtplib

from .base_dispatcher import BaseDispatcher

logger = logging.getLogger(__name__)


class EmailDispatcher(BaseDispatcher):
    """Dispatcher for sending notifications via email."""

    async def send(
        self,
        address: str,
        level: str,
        message: str,
        metadata: Dict[str, Any] = None,
        smtp_config: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """
        Send a notification email.

        Args:
            address: Email address to send to
            level: Notification level (INFO, WARNING, ERROR)
            message: Notification message content
            metadata: Optional additional metadata
            smtp_config: SMTP configuration dict with keys:
                - smtp_host: SMTP server hostname
                - smtp_port: SMTP server port (default 587)
                - smtp_user: SMTP username
                - smtp_password: SMTP password
                - smtp_sender: From address
                - smtp_use_tls: Use TLS encryption (default True)

        Returns:
            Tuple of (success: bool, details: str)
        """
        # Validate SMTP configuration
        if not smtp_config:
            error_msg = "SMTP configuration is required for email notifications"
            logger.error(error_msg)
            return False, error_msg

        smtp_host = smtp_config.get('smtp_host')
        smtp_port = smtp_config.get('smtp_port', 587)
        smtp_user = smtp_config.get('smtp_user')
        smtp_password = smtp_config.get('smtp_password')
        smtp_sender = smtp_config.get('smtp_sender')
        smtp_use_tls = smtp_config.get('smtp_use_tls', True)

        # Check required fields
        if not smtp_host:
            error_msg = "SMTP host is not configured for this notifier"
            logger.error(error_msg)
            return False, error_msg

        if not smtp_sender:
            error_msg = "SMTP sender is not configured for this notifier"
            logger.error(error_msg)
            return False, error_msg

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"File Fridge Notification - {level.upper()}"
            msg["From"] = smtp_sender
            msg["To"] = address

            # Format message body
            body = self._format_message(level, message, metadata)

            # Add plain text part
            text_part = MIMEText(body, "plain")
            msg.attach(text_part)

            # Add HTML part for better formatting
            html_body = self._format_html_message(level, message, metadata)
            html_part = MIMEText(html_body, "html")
            msg.attach(html_part)

            # Send email
            logger.info(f"Sending email notification to {address} via {smtp_host}")

            await aiosmtplib.send(
                msg,
                hostname=smtp_host,
                port=smtp_port,
                username=smtp_user,
                password=smtp_password,
                use_tls=smtp_use_tls,
            )

            success_msg = f"Email sent successfully to {address}"
            logger.info(success_msg)
            return True, success_msg

        except aiosmtplib.SMTPException as e:
            error_msg = f"SMTP error sending email to {address}: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Unexpected error sending email to {address}: {str(e)}"
            logger.exception(error_msg)
            return False, error_msg

    def _format_html_message(self, level: str, message: str, metadata: Dict[str, Any] = None) -> str:
        """
        Format a notification message as HTML.

        Args:
            level: Notification level
            message: Message content
            metadata: Optional metadata

        Returns:
            HTML formatted message
        """
        # Color coding based on level
        color_map = {
            "INFO": "#2196F3",  # Blue
            "WARNING": "#FF9800",  # Orange
            "ERROR": "#F44336",  # Red
        }
        color = color_map.get(level.upper(), "#666666")

        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background-color: {color}; color: white; padding: 10px 20px; border-radius: 5px 5px 0 0;">
                        <h2 style="margin: 0;">File Fridge Notification</h2>
                    </div>
                    <div style="background-color: #f5f5f5; padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 5px 5px;">
                        <p style="background-color: white; padding: 10px; border-left: 4px solid {color}; margin: 0 0 15px 0;">
                            <strong>Level:</strong> <span style="color: {color};">{level.upper()}</span>
                        </p>
                        <div style="background-color: white; padding: 15px; border-radius: 3px;">
                            <p style="margin: 0;"><strong>Message:</strong></p>
                            <p style="margin: 10px 0 0 0;">{message}</p>
                        </div>
        """

        if metadata:
            html += """
                        <div style="background-color: white; padding: 15px; border-radius: 3px; margin-top: 15px;">
                            <p style="margin: 0 0 10px 0;"><strong>Additional Information:</strong></p>
                            <table style="width: 100%; border-collapse: collapse;">
            """
            for key, value in metadata.items():
                html += f"""
                                <tr>
                                    <td style="padding: 5px; border-bottom: 1px solid #eee; color: #666;">{key}:</td>
                                    <td style="padding: 5px; border-bottom: 1px solid #eee;">{value}</td>
                                </tr>
                """
            html += """
                            </table>
                        </div>
            """

        html += """
                        <p style="margin-top: 20px; font-size: 12px; color: #999; text-align: center;">
                            This is an automated notification from File Fridge
                        </p>
                    </div>
                </div>
            </body>
        </html>
        """

        return html
