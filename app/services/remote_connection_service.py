import logging
import secrets
import uuid
from typing import List, Optional

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.config import settings
from app.models import InstanceMetadata, RemoteConnection
from app.schemas import RemoteConnectionCreate, RemoteConnectionUpdate
from app.utils.remote_auth import remote_auth

logger = logging.getLogger(__name__)


class RemoteConnectionService:
    """Service for managing remote File Fridge connections."""

    def get_instance_uuid(self, db: Session) -> str:
        """Get the global instance UUID."""
        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)
            db.commit()
            db.refresh(metadata)
        return metadata.instance_uuid

    async def connect_to_remote(
        self, db: Session, connection_data: RemoteConnectionCreate
    ) -> RemoteConnection:
        """Initiate connection to a remote File Fridge instance."""
        # Generate a shared secret
        shared_secret = secrets.token_hex(32)

        # Get our own URL and UUID
        my_url = settings.ff_instance_url or "http://localhost:8000"  # Fallback if not set
        my_name = settings.instance_name or settings.app_name
        my_uuid = self.get_instance_uuid(db)

        # Call the remote instance's handshake endpoint
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{connection_data.url.rstrip('/')}/api/remote/handshake",
                    json={
                        "name": my_name,
                        "url": my_url,
                        "instance_uuid": my_uuid,
                        "connection_code": connection_data.connection_code,
                        "shared_secret": shared_secret,
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
                remote_data = response.json()
                remote_instance_uuid = remote_data.get("instance_uuid")
            except httpx.HTTPError as e:
                msg = f"Could not connect to remote instance: {e}"
                logger.exception("Failed to connect to remote instance at %s", connection_data.url)
                raise ValueError(msg) from e

        # Store the connection locally
        remote_conn = RemoteConnection(
            name=connection_data.name,
            url=connection_data.url,
            remote_instance_uuid=remote_instance_uuid,
            shared_secret=shared_secret,
        )
        db.add(remote_conn)
        db.commit()
        db.refresh(remote_conn)
        return remote_conn

    def handle_handshake(self, db: Session, handshake_data: dict) -> dict:
        """Handle an incoming handshake request from a remote instance."""
        # Verify connection code
        if handshake_data["connection_code"] != remote_auth.get_code():
            msg = "Invalid connection code"
            raise ValueError(msg)

        remote_uuid = handshake_data.get("instance_uuid")
        remote_url = handshake_data["url"]

        # Check if connection already exists by UUID (preferred) or URL (fallback)
        existing = None
        if remote_uuid:
            existing = (
                db.query(RemoteConnection)
                .filter(RemoteConnection.remote_instance_uuid == remote_uuid)
                .first()
            )

        if not existing:
            existing = db.query(RemoteConnection).filter(RemoteConnection.url == remote_url).first()

        if existing:
            # Update existing connection
            existing.name = handshake_data["name"]
            existing.url = remote_url
            existing.remote_instance_uuid = remote_uuid
            existing.shared_secret = handshake_data["shared_secret"]
            db.commit()
            db.refresh(existing)
            return {"status": "success", "instance_uuid": self.get_instance_uuid(db)}

        # Create new connection
        remote_conn = RemoteConnection(
            name=handshake_data["name"],
            url=remote_url,
            remote_instance_uuid=remote_uuid,
            shared_secret=handshake_data["shared_secret"],
        )
        db.add(remote_conn)
        db.commit()
        return {"status": "success", "instance_uuid": self.get_instance_uuid(db)}

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
            my_uuid = self.get_instance_uuid(db)
            async with httpx.AsyncClient() as client:
                try:
                    await client.post(
                        f"{conn.url.rstrip('/')}/api/remote/terminate-connection",
                        headers={
                            "X-Instance-UUID": my_uuid,
                            "X-Shared-Secret": conn.shared_secret,
                        },
                        json={"instance_uuid": my_uuid},
                        timeout=5.0,
                    )
                except Exception as e:
                    msg = "Could not notify remote instance. Use 'force' to delete anyway."
                    logger.warning(f"Failed to notify remote instance of deletion: {e}")
                    raise ValueError(msg) from e

        db.delete(conn)
        db.commit()

    async def update_connection(
        self, db: Session, connection_id: int, update_data: RemoteConnectionUpdate
    ) -> RemoteConnection:
        """Update a remote connection, verifying the new URL if provided."""
        conn = self.get_connection(db, connection_id)
        if not conn:
            raise ValueError("Connection not found")

        if update_data.name is not None:
            conn.name = update_data.name

        if update_data.url is not None and update_data.url != conn.url:
            # Verify new URL via challenge-response
            await self._verify_remote_url(db, conn, update_data.url)
            conn.url = update_data.url

        db.commit()
        db.refresh(conn)
        return conn

    async def _verify_remote_url(self, db: Session, conn: RemoteConnection, new_url: str):
        """Perform challenge-response verification for a new URL."""
        # 1. Generate random challenge
        challenge = secrets.token_hex(16)

        # 2. Encrypt challenge with shared secret
        try:
            key = bytes.fromhex(conn.shared_secret)[:32]
            aesgcm = AESGCM(key)
            nonce = secrets.token_bytes(12)
            encrypted = aesgcm.encrypt(nonce, challenge.encode(), None)
            challenge_payload = nonce.hex() + encrypted.hex()
        except Exception as e:
            logger.exception("Failed to encrypt challenge")
            raise ValueError(f"Failed to prepare verification challenge: {e}") from e

        # 3. Send to remote challenge endpoint
        my_uuid = self.get_instance_uuid(db)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{new_url.rstrip('/')}/api/remote/challenge",
                    json={"initiator_uuid": my_uuid, "challenge": challenge_payload},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()

                # 4. Verify response matches original challenge
                if data.get("decrypted") != challenge:
                    raise ValueError(
                        "Challenge verification failed: incorrect response from remote"
                    )

            except httpx.HTTPError as e:
                logger.warning(f"Failed to verify remote URL {new_url}: {e}")
                raise ValueError(
                    f"Could not verify remote instance at {new_url}. "
                    "Ensure the shared secret is still valid and the server is reachable."
                ) from e
            except Exception as e:
                logger.exception("Unexpected error during URL verification")
                raise ValueError(f"Verification failed: {e}") from e

    def handle_challenge(self, db: Session, initiator_uuid: str, encrypted_challenge: str) -> str:
        """Decrypt a challenge from a remote instance."""
        # Find connection by initiator UUID
        conn = (
            db.query(RemoteConnection)
            .filter(RemoteConnection.remote_instance_uuid == initiator_uuid)
            .first()
        )
        if not conn:
            raise ValueError("No connection found for this UUID")

        try:
            # Extract nonce (first 12 bytes / 24 hex chars)
            nonce = bytes.fromhex(encrypted_challenge[:24])
            ciphertext = bytes.fromhex(encrypted_challenge[24:])

            # Decrypt
            key = bytes.fromhex(conn.shared_secret)[:32]
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted.decode()
        except Exception as e:
            logger.warning(f"Decryption failed for challenge from {initiator_uuid}: {e}")
            raise ValueError("Decryption failed") from e

    def handle_terminate_connection(self, db: Session, remote_uuid: str):
        """Handle an incoming termination request."""
        conn = (
            db.query(RemoteConnection)
            .filter(RemoteConnection.remote_instance_uuid == remote_uuid)
            .first()
        )
        if conn:
            db.delete(conn)
            db.commit()


remote_connection_service = RemoteConnectionService()
