"""Service for managing remote File Fridge connections."""
import json
import logging
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RemoteConnection, TrustStatus
from app.schemas import RemoteConnectionIdentity
from app.services.identity_service import identity_service

logger = logging.getLogger(__name__)


def canonical_json_encode(data: dict) -> bytes:
    """Encode dict as canonical JSON for signing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RemoteConnectionService:
    """Service for managing remote File Fridge connections."""

    def list_connections(self, db: Session) -> List[RemoteConnection]:
        """List all remote connections."""
        return db.query(RemoteConnection).all()

    def get_connection(self, db: Session, connection_id: int) -> Optional[RemoteConnection]:
        """Get a specific remote connection."""
        return db.query(RemoteConnection).filter(RemoteConnection.id == connection_id).first()

    def get_connection_by_fingerprint(
        self, db: Session, fingerprint: str
    ) -> Optional[RemoteConnection]:
        """Get a remote connection by its public key fingerprint."""
        return (
            db.query(RemoteConnection).filter(RemoteConnection.remote_fingerprint == fingerprint).first()
        )

    async def get_remote_identity(self, remote_url: str) -> RemoteConnectionIdentity:
        """
        Fetch the public identity of a remote File Fridge instance.
        This is the first step of the connection handshake.
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{remote_url.rstrip('/')}/api/remote/identity", timeout=10.0
                )
                response.raise_for_status()
                identity_data = response.json()
                # TODO: Add validation with a Pydantic model
                return RemoteConnectionIdentity(**identity_data)
            except httpx.HTTPError as e:
                msg = f"Could not fetch identity from remote instance: {e}"
                logger.exception("Failed to fetch identity from %s", remote_url)
                raise ValueError(msg) from e
            except Exception as e:
                msg = f"An unexpected error occurred while fetching identity: {e}"
                logger.exception("Unexpected error fetching identity from %s", remote_url)
                raise ValueError(msg) from e

    async def initiate_connection(
        self, db: Session, name: str, remote_identity: RemoteConnectionIdentity
    ) -> RemoteConnection:
        """
        Create a new trusted remote connection and notify the remote instance.
        This is the second step, taken after the user has verified the remote's identity.
        """
        # 1. Check if connection already exists
        existing_conn = self.get_connection_by_fingerprint(db, remote_identity.fingerprint)
        if existing_conn:
            if existing_conn.trust_status == TrustStatus.TRUSTED:
                return existing_conn
            # If pending or rejected, we can update and proceed
            existing_conn.name = name
            existing_conn.url = remote_identity.url
            existing_conn.trust_status = TrustStatus.TRUSTED
            db.commit()
            db.refresh(existing_conn)
            return existing_conn

        # 2. Create and save the new connection locally as TRUSTED
        new_conn = RemoteConnection(
            name=name,
            url=remote_identity.url,
            remote_fingerprint=remote_identity.fingerprint,
            remote_ed25519_public_key=remote_identity.ed25519_public_key,
            remote_x25519_public_key=remote_identity.x25519_public_key,
            trust_status=TrustStatus.TRUSTED,
        )
        db.add(new_conn)
        db.commit()
        db.refresh(new_conn)

        # 3. Send our identity to the remote to establish a PENDING connection there
        my_identity_payload = {
            "instance_name": settings.instance_name or "File Fridge",
            "fingerprint": identity_service.get_instance_fingerprint(db),
            "ed25519_public_key": identity_service.get_signing_public_key_str(db),
            "x25519_public_key": identity_service.get_kx_public_key_str(db),
            "url": settings.ff_instance_url,
        }

        # Sign the payload
        message_to_sign = canonical_json_encode(my_identity_payload)
        signature = identity_service.sign_message(db, message_to_sign)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{remote_identity.url.rstrip('/')}/api/remote/connection-request",
                    json={"identity": my_identity_payload, "signature": signature.hex()},
                    timeout=10.0,
                )
                response.raise_for_status()
                # The response from the remote also contains its signed identity,
                # which we can verify to prevent man-in-the-middle attacks.
                remote_response = response.json()
                self._verify_remote_response(remote_identity, remote_response)

            except Exception as e:
                # If the notification fails, we still keep the local connection.
                # The user can retry later. We can add a status field for this.
                logger.error(
                    "Failed to send connection request to remote instance %s: %s", name, e
                )
                # Rollback or mark as "local_only"? For now, we'll keep it.

        return new_conn

    def _verify_remote_response(self, original_identity, response_data):
        """Verify the signature in the response from a remote instance."""
        response_identity = response_data.get("identity", {})
        response_signature_hex = response_data.get("signature")

        # Check if the fingerprint matches the one we originally trusted
        if response_identity.get("fingerprint") != original_identity.fingerprint:
            raise ValueError("Man-in-the-middle attack suspected! Fingerprint mismatch.")

        # Verify the signature
        message_to_verify = canonical_json_encode(response_identity)
        signature = bytes.fromhex(response_signature_hex)

        if not identity_service.verify_signature(
            original_identity.ed25519_public_key, signature, message_to_verify
        ):
            raise ValueError("Signature verification of remote response failed.")

        logger.info("Successfully verified remote instance identity.")

    def handle_connection_request(self, db: Session, request_data: dict) -> dict:
        """
        Handle an incoming connection request from a remote instance.
        If the request is valid, create a PENDING connection.
        """
        identity = request_data.get("identity", {})
        signature_hex = request_data.get("signature")

        if not all([identity, signature_hex]):
            raise ValueError("Incomplete connection request data.")

        # 1. Verify the signature
        message_to_verify = canonical_json_encode(identity)
        signature = bytes.fromhex(signature_hex)
        if not identity_service.verify_signature(
            identity.get("ed25519_public_key"), signature, message_to_verify
        ):
            raise ValueError("Signature verification failed for connection request.")

        # 2. Create or update the connection as PENDING
        fingerprint = identity.get("fingerprint")
        conn = self.get_connection_by_fingerprint(db, fingerprint)
        if not conn:
            conn = RemoteConnection(
                name=identity.get("instance_name"),
                url=identity.get("url"),
                remote_fingerprint=fingerprint,
                remote_ed25519_public_key=identity.get("ed25519_public_key"),
                remote_x25519_public_key=identity.get("x25519_public_key"),
                trust_status=TrustStatus.PENDING,
            )
            db.add(conn)
        else:
            # Update info but keep trust status as is, unless it was rejected.
            conn.name = identity.get("instance_name")
            conn.url = identity.get("url")
            if conn.trust_status == TrustStatus.REJECTED:
                conn.trust_status = TrustStatus.PENDING

        db.commit()

        # 3. Return our own signed identity to prove who we are
        my_identity_payload = {
            "instance_name": settings.instance_name or "File Fridge",
            "fingerprint": identity_service.get_instance_fingerprint(db),
            "ed25519_public_key": identity_service.get_signing_public_key_str(db),
            "x25519_public_key": identity_service.get_kx_public_key_str(db),
            "url": settings.ff_instance_url,
        }
        message_to_sign = canonical_json_encode(my_identity_payload)
        my_signature = identity_service.sign_message(db, message_to_sign)

        return {"identity": my_identity_payload, "signature": my_signature.hex()}

    def trust_connection(self, db: Session, connection_id: int) -> RemoteConnection:
        """Manually trust a PENDING connection."""
        conn = self.get_connection(db, connection_id)
        if not conn:
            raise ValueError("Connection not found")
        if conn.trust_status != TrustStatus.PENDING:
            logger.warning(
                "Attempted to trust a connection that is not pending (status: %s)",
                conn.trust_status.value,
            )
        conn.trust_status = TrustStatus.TRUSTED
        db.commit()
        db.refresh(conn)
        return conn

    async def delete_connection(self, db: Session, connection_id: int):
        """Delete a remote connection and notify the remote instance."""
        conn = self.get_connection(db, connection_id)
        if not conn:
            return

        if conn.trust_status == TrustStatus.TRUSTED:
            # Notify remote instance of termination
            from app.utils.remote_signature import get_signed_headers

            try:
                url = f"{conn.url.rstrip('/')}/api/remote/terminate-connection"
                headers = await get_signed_headers(db, "POST", url, b"")

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json={})
                    response.raise_for_status()
                    logger.info(f"Successfully notified {conn.name} of connection termination")
            except Exception as e:
                logger.warning(
                    f"Failed to notify remote instance {conn.name} of termination: {e}. "
                    "Proceeding with local deletion."
                )
                # Continue with deletion even if notification fails

        # Delete locally
        db.delete(conn)
        db.commit()

    def handle_terminate_connection(self, db: Session, remote_fingerprint: str):
        """Handle an incoming termination request from a remote instance."""
        conn = self.get_connection_by_fingerprint(db, remote_fingerprint)
        if not conn:
            logger.warning(f"Received termination for unknown fingerprint: {remote_fingerprint}")
            return

        # Mark connection as rejected rather than deleting
        # (preserves history, prevents auto-reconnect)
        conn.trust_status = TrustStatus.REJECTED
        db.commit()
        logger.info(f"Connection with {conn.name} terminated by remote request")


remote_connection_service = RemoteConnectionService()
