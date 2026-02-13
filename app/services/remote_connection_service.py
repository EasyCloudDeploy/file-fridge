"""Service for managing remote File Fridge connections."""

import json
import logging
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models import RemoteConnection, TransferMode, TrustStatus
from app.schemas import (
    RemoteConnectionIdentity,
    RemoteConnectionRequest,
    RemoteConnectionResponse,
)
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
        logger.debug(f"Looking up remote connection by fingerprint: {fingerprint}")
        conn = (
            db.query(RemoteConnection)
            .filter(RemoteConnection.remote_fingerprint == fingerprint)
            .first()
        )
        if conn:
            logger.debug(f"Found remote connection: {conn.name} (ID: {conn.id})")
        else:
            logger.debug("Remote connection not found")
        return conn

    async def get_remote_identity(self, remote_url: str) -> RemoteConnectionIdentity:
        """
        Fetch the public identity of a remote File Fridge instance.
        This is the first step of the connection handshake.
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{remote_url.rstrip('/')}/api/v1/remote/identity", timeout=10.0
                )
                response.raise_for_status()
                identity_data = response.json()
                # Validate with Pydantic model
                return RemoteConnectionIdentity.model_validate(identity_data)
            except httpx.HTTPError as e:
                msg = f"Could not fetch identity from remote instance: {e}"
                logger.exception("Failed to fetch identity from %s", remote_url)
                raise ValueError(msg) from e
            except Exception as e:
                msg = f"An unexpected error occurred while fetching identity: {e}"
                logger.exception("Unexpected error fetching identity from %s", remote_url)
                raise ValueError(msg) from e

    async def initiate_connection(
        self,
        db: Session,
        name: str,
        remote_identity: RemoteConnectionIdentity,
        connection_code: Optional[str] = None,
        transfer_mode: TransferMode = TransferMode.PUSH_ONLY,
    ) -> RemoteConnection:
        """
        Create a new trusted remote connection and notify the remote instance.
        This is the second step, taken after the user has verified the remote's identity.

        Args:
            db: Database session
            name: Local name for this connection
            remote_identity: Identity information from the remote instance
            connection_code: Optional connection code to authenticate with the remote
        """
        # 0. Verify instance URL is configured
        from app.services.instance_config_service import instance_config_service

        instance_url = instance_config_service.get_instance_url(db)
        if not instance_url:
            raise ValueError(
                "Instance URL not configured. Please set FF_INSTANCE_URL environment variable "
                "or configure it via the UI to enable remote connections."
            )

        # 1. Check if connection already exists
        conn = self.get_connection_by_fingerprint(db, remote_identity.fingerprint)
        is_new_connection = conn is None

        if is_new_connection:
            # 2. Create and save the new connection locally as TRUSTED
            conn = RemoteConnection(
                name=name,
                url=str(remote_identity.url),
                remote_fingerprint=remote_identity.fingerprint,
                remote_ed25519_public_key=remote_identity.ed25519_public_key,
                remote_x25519_public_key=remote_identity.x25519_public_key,
                trust_status=TrustStatus.TRUSTED,
                transfer_mode=transfer_mode,
            )
            db.add(conn)
        else:
            # Update existing connection info
            conn.name = name
            conn.url = str(remote_identity.url)
            conn.trust_status = TrustStatus.TRUSTED
            conn.transfer_mode = transfer_mode

        db.commit()
        db.refresh(conn)

        # 3. Send our identity to the remote to establish a PENDING connection there
        instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
        my_identity_payload = {
            "instance_name": instance_name,
            "fingerprint": identity_service.get_instance_fingerprint(db),
            "ed25519_public_key": identity_service.get_signing_public_key_str(db),
            "x25519_public_key": identity_service.get_kx_public_key_str(db),
            "url": instance_url,
            "transfer_mode": conn.transfer_mode.value,
        }

        # Sign the payload
        message_to_sign = canonical_json_encode(my_identity_payload)
        signature = identity_service.sign_message(db, message_to_sign)

        # Build request payload
        request_payload = {"identity": my_identity_payload, "signature": signature.hex()}
        if connection_code:
            request_payload["connection_code"] = connection_code

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{remote_identity.url.rstrip('/')}/api/v1/remote/connection-request",
                    json=request_payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                # The response from the remote also contains its signed identity,
                # which we can verify to prevent man-in-the-middle attacks.
                remote_response_data = response.json()
                remote_response = RemoteConnectionResponse.model_validate(remote_response_data)
                self._verify_remote_response(remote_identity, remote_response)

                # 4. Update remote's transfer mode from their response
                remote_mode = remote_response.identity.transfer_mode
                if remote_mode:
                    conn.remote_transfer_mode = remote_mode
                    db.commit()
                    db.refresh(conn)

            except httpx.HTTPStatusError as e:
                # If we get a 401/403, it's likely a connection code issue
                if e.response.status_code in (401, 403):
                    # Rollback the local connection since the remote rejected us
                    if is_new_connection:
                        db.delete(conn)
                        db.commit()
                    raise ValueError(
                        "Connection rejected by remote instance. "
                        "The connection code may be invalid or expired."
                    ) from e
                # Re-raise other HTTP errors
                raise
            except Exception as e:
                # If the notification fails, we still keep the local connection.
                # The user can retry later. We can add a status field for this.
                logger.error("Failed to send connection request to remote instance %s: %s", name, e)
                # Rollback or mark as "local_only"? For now, we'll keep it.

        return conn

    def _verify_remote_response(
        self,
        original_identity: RemoteConnectionIdentity,
        response: RemoteConnectionResponse,
    ):
        """Verify the signature in the response from a remote instance."""
        # Check if the fingerprint matches the one we originally trusted
        if response.identity.fingerprint != original_identity.fingerprint:
            raise ValueError("Man-in-the-middle attack suspected! Fingerprint mismatch.")

        # Verify the signature
        # We use model_dump(exclude_unset=True) to get the dict for signing
        # to ensure we only include fields that were actually present in the response.
        message_to_verify = canonical_json_encode(response.identity.model_dump(exclude_unset=True))
        signature = bytes.fromhex(response.signature)

        if not identity_service.verify_signature(
            original_identity.ed25519_public_key, signature, message_to_verify
        ):
            raise ValueError("Signature verification of remote response failed.")

        logger.info("Successfully verified remote instance identity.")

    def handle_connection_request(self, db: Session, request_data: dict) -> dict:
        """
        Handle an incoming connection request from a remote instance.
        If the request is valid, create a PENDING connection.

        If a connection_code is provided in the request, it will be verified
        before proceeding. This allows authenticated connection establishment.
        """
        # Validate request data with Pydantic model
        request = RemoteConnectionRequest.model_validate(request_data)
        identity = request.identity
        signature_hex = request.signature
        connection_code = request.connection_code

        # Verify instance URL is configured
        from app.services.instance_config_service import instance_config_service

        instance_url = instance_config_service.get_instance_url(db)
        if not instance_url:
            raise ValueError(
                "Instance URL not configured. Please set FF_INSTANCE_URL environment variable "
                "or configure it via the UI to enable remote connections."
            )

        # 1. Verify the connection code if provided
        if connection_code:
            from app.utils.remote_auth import remote_auth

            current_code = remote_auth.get_code()
            if connection_code != current_code:
                raise ValueError("Invalid or expired connection code.")

        # 2. Verify the signature
        message_to_verify = canonical_json_encode(identity.model_dump(exclude_unset=True))
        signature = bytes.fromhex(signature_hex)
        if not identity_service.verify_signature(
            identity.ed25519_public_key, signature, message_to_verify
        ):
            raise ValueError("Signature verification failed for connection request.")

        # 3. Create or update the connection as PENDING
        fingerprint = identity.fingerprint
        conn = self.get_connection_by_fingerprint(db, fingerprint)
        # Parse remote transfer mode from identity payload
        remote_mode = identity.transfer_mode or TransferMode.PUSH_ONLY

        if not conn:
            conn = RemoteConnection(
                name=identity.instance_name,
                url=str(identity.url),
                remote_fingerprint=fingerprint,
                remote_ed25519_public_key=identity.ed25519_public_key,
                remote_x25519_public_key=identity.x25519_public_key,
                trust_status=TrustStatus.PENDING,
                remote_transfer_mode=remote_mode,
            )
            db.add(conn)
        else:
            # Update info but keep trust status as is, unless it was rejected.
            conn.name = identity.instance_name
            conn.url = str(identity.url)
            conn.remote_transfer_mode = remote_mode
            if conn.trust_status == TrustStatus.REJECTED:
                conn.trust_status = TrustStatus.PENDING

        db.commit()

        # 4. Return our own signed identity to prove who we are
        instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
        my_identity_payload = {
            "instance_name": instance_name,
            "fingerprint": identity_service.get_instance_fingerprint(db),
            "ed25519_public_key": identity_service.get_signing_public_key_str(db),
            "x25519_public_key": identity_service.get_kx_public_key_str(db),
            "url": instance_url,
            "transfer_mode": conn.transfer_mode.value,
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

    def reject_connection(self, db: Session, connection_id: int) -> RemoteConnection:
        """Reject a PENDING connection."""
        conn = self.get_connection(db, connection_id)
        if not conn:
            raise ValueError("Connection not found")
        if conn.trust_status != TrustStatus.PENDING:
            logger.warning(
                "Attempted to reject a connection that is not pending (status: %s)",
                conn.trust_status.value,
            )
        conn.trust_status = TrustStatus.REJECTED
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
                url = f"{conn.url.rstrip('/')}/api/v1/remote/terminate-connection"
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

    async def notify_transfer_mode_change(self, db: Session, conn: RemoteConnection):
        """Notify the remote instance that our transfer mode has changed."""
        from app.utils.remote_signature import get_signed_headers

        url = f"{conn.url.rstrip('/')}/api/v1/remote/sync-transfer-mode"
        payload = {"transfer_mode": conn.transfer_mode.value}
        body_bytes = canonical_json_encode(payload)
        headers = await get_signed_headers(db, "POST", url, body_bytes)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=headers,
                content=body_bytes,
            )
            response.raise_for_status()
            return response.json()

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
