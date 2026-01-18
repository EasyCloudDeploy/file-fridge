import logging
import secrets
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RemoteConnection
from app.schemas import RemoteConnectionCreate
from app.utils.remote_auth import remote_auth

logger = logging.getLogger(__name__)


class RemoteConnectionService:
    """Service for managing remote File Fridge connections."""

    async def connect_to_remote(
        self, db: Session, connection_data: RemoteConnectionCreate
    ) -> RemoteConnection:
        """Initiate connection to a remote File Fridge instance."""
        # Generate a shared secret
        shared_secret = secrets.token_hex(32)

        # Get our own URL
        my_url = settings.ff_instance_url or "http://localhost:8000"  # Fallback if not set

        # Call the remote instance's handshake endpoint
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{connection_data.url.rstrip('/')}/api/remote/handshake",
                    json={
                        "name": settings.app_name,
                        "url": my_url,
                        "connection_code": connection_data.connection_code,
                        "shared_secret": shared_secret,
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                msg = f"Could not connect to remote instance: {e}"
                logger.exception(
                    f"Failed to connect to remote instance at {connection_data.url}: {e}"
                )
                raise ValueError(msg) from e

        # Store the connection locally
        remote_conn = RemoteConnection(
            name=connection_data.name, url=connection_data.url, shared_secret=shared_secret
        )
        db.add(remote_conn)
        db.commit()
        db.refresh(remote_conn)
        return remote_conn

    def handle_handshake(self, db: Session, handshake_data: dict) -> RemoteConnection:
        """Handle an incoming handshake request from a remote instance."""
        # Verify connection code
        if handshake_data["connection_code"] != remote_auth.get_code():
            msg = "Invalid connection code"
            raise ValueError(msg)

        # Check if connection already exists
        existing = (
            db.query(RemoteConnection).filter(RemoteConnection.url == handshake_data["url"]).first()
        )
        if existing:
            # Update existing connection
            existing.name = handshake_data["name"]
            existing.shared_secret = handshake_data["shared_secret"]
            db.commit()
            db.refresh(existing)
            return existing

        # Create new connection
        remote_conn = RemoteConnection(
            name=handshake_data["name"],
            url=handshake_data["url"],
            shared_secret=handshake_data["shared_secret"],
        )
        db.add(remote_conn)
        db.commit()
        db.refresh(remote_conn)
        return remote_conn

    def list_connections(self, db: Session) -> List[RemoteConnection]:
        """List all remote connections."""
        return db.query(RemoteConnection).all()

    def get_connection(self, db: Session, connection_id: int) -> Optional[RemoteConnection]:
        """Get a specific remote connection."""
        return db.query(RemoteConnection).filter(RemoteConnection.id == connection_id).first()

    async def delete_connection(self, db: Session, connection_id: int, force: bool = False):
        """Delete a remote connection."""
        conn = self.get_connection(db, connection_id)
        if not conn:
            return

        if not force:
            # Try to notify the remote instance
            async with httpx.AsyncClient() as client:
                try:
                    # In a real scenario, we'd use the shared secret for auth
                    # For now, let's assume an endpoint exists
                    await client.post(
                        f"{conn.url.rstrip('/')}/api/remote/terminate-connection",
                        headers={
                            "X-Remote-ID": str(conn.id),
                            "X-Shared-Secret": conn.shared_secret,
                        },
                        json={"url": settings.ff_instance_url or "http://localhost:8000"},
                        timeout=5.0,
                    )
                except Exception as e:
                    msg = "Could not notify remote instance. Use 'force' to delete anyway."
                    logger.warning(f"Failed to notify remote instance of deletion: {e}")
                    raise ValueError(msg) from e

        db.delete(conn)
        db.commit()

    def handle_terminate_connection(self, db: Session, remote_url: str):
        """Handle an incoming termination request."""
        conn = db.query(RemoteConnection).filter(RemoteConnection.url == remote_url).first()
        if conn:
            db.delete(conn)
            db.commit()


remote_connection_service = RemoteConnectionService()
