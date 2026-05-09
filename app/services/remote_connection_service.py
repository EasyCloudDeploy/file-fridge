"""Service for managing remote File Fridge connections."""

import json
import logging
from typing import Any, List, Optional

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

    @staticmethod
    def _base_url(url_value: object) -> str:
        """Normalize URL-like values (e.g., Pydantic HttpUrl) to a clean base URL string."""
        return str(url_value).rstrip("/")

    @staticmethod
    def _verify_identity_signature(
        *,
        public_key_b64: str,
        signature_hex: str,
        parsed_identity: RemoteConnectionIdentity,
        raw_identity_payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Verify identity signature with compatibility for URL canonicalization differences.

        Some peers sign the raw JSON identity payload, while others may sign a normalized
        model serialization. Accept either to avoid handshake failures between versions.
        """
        signature = bytes.fromhex(signature_hex)
        messages_to_try: list[bytes] = []

        if raw_identity_payload is not None:
            messages_to_try.append(canonical_json_encode(raw_identity_payload))

        messages_to_try.append(
            canonical_json_encode(parsed_identity.model_dump(mode="json", exclude_unset=True))
        )

        seen: set[bytes] = set()
        for message in messages_to_try:
            if message in seen:
                continue
            seen.add(message)
            if identity_service.verify_signature(public_key_b64, signature, message):
                return True

        return False

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
                    f"{self._base_url(remote_url)}/api/v1/remote/identity", timeout=10.0
                )
                response.raise_for_status()
                identity_data = response.json()
                # Validate with Pydantic model
                return RemoteConnectionIdentity.model_validate(identity_data)
            except (httpx.ConnectError, httpx.UnsupportedProtocol):
                raise ValueError(
                    f"Could not connect to '{remote_url}'. Check that the URL is correct and the remote instance is running."
                )
            except httpx.ConnectTimeout:
                raise ValueError(
                    "Connection timed out after 10s. Check network connectivity and firewall rules."
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 500:
                    raise ValueError(
                        "The remote instance encountered an internal error. Check its logs."
                    )
                raise ValueError(f"Remote instance returned an error: {e.response.status_code}")
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

        # 1. Check if connection already exists. We only persist changes
        # after the remote handshake fully succeeds.
        existing_conn = self.get_connection_by_fingerprint(db, remote_identity.fingerprint)

        # 3. Send our identity to the remote to establish a PENDING connection there
        instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
        my_identity_payload = {
            "instance_name": instance_name,
            "fingerprint": identity_service.get_instance_fingerprint(db),
            "ed25519_public_key": identity_service.get_signing_public_key_str(db),
            "x25519_public_key": identity_service.get_kx_public_key_str(db),
            "url": instance_url,
            "transfer_mode": transfer_mode.value,
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
                remote_base_url = self._base_url(remote_identity.url)
                response = await client.post(
                    f"{remote_base_url}/api/v1/remote/connection-request",
                    json=request_payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                # The response from the remote also contains its signed identity,
                # which we can verify to prevent man-in-the-middle attacks.
                remote_response_data = response.json()
                remote_response = RemoteConnectionResponse.model_validate(remote_response_data)
                raw_remote_identity = remote_response_data.get("identity")
                self._verify_remote_response(
                    remote_identity,
                    remote_response,
                    raw_remote_identity if isinstance(raw_remote_identity, dict) else None,
                )

                # 4. Persist local connection only after successful handshake verification.
                if existing_conn is None:
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
                    conn = existing_conn
                    conn.name = name
                    conn.url = str(remote_identity.url)
                    conn.trust_status = TrustStatus.TRUSTED
                    conn.transfer_mode = transfer_mode

                remote_mode = remote_response.identity.transfer_mode
                if remote_mode:
                    conn.remote_transfer_mode = remote_mode
                remote_trust = remote_response.trust_status
                logger.info(
                    "Remote instance reports connection trust status: %s",
                    remote_trust or "not reported (legacy)",
                )
                db.commit()
                db.refresh(conn)

            except httpx.ConnectTimeout as e:
                raise ValueError(
                    "Connection timed out after 10s. Check network connectivity and firewall rules."
                ) from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise ValueError(
                        "Connection rejected — the connection code is invalid or has expired. Get a fresh code from the remote instance."
                    ) from e
                if e.response.status_code == 500:
                    raise ValueError(
                        "The remote instance encountered an internal error. Check its logs."
                    ) from e
                # Surface the remote's error message for other HTTP failures
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except Exception:
                    error_detail = str(e)
                raise ValueError(f"Remote instance returned an error: {error_detail}") from e
            except Exception as e:
                # Capture the original error message to check for specific failure types
                error_msg = str(e)
                if "Fingerprint verification failed" in error_msg:
                    raise ValueError(
                        "Fingerprint verification failed. The remote instance may have changed its identity since you last connected."
                    ) from e

                logger.error("Failed to send connection request to remote instance %s: %s", name, e)
                raise ValueError(
                    f"Could not connect to '{remote_identity.url}'. Check that the URL is correct and the remote instance is running."
                ) from e

        return conn

    def _verify_remote_response(
        self,
        original_identity: RemoteConnectionIdentity,
        response: RemoteConnectionResponse,
        raw_identity_payload: Optional[dict[str, Any]] = None,
    ):
        """Verify the signature in the response from a remote instance."""
        # Check if the fingerprint matches the one we originally trusted
        if response.identity.fingerprint != original_identity.fingerprint:
            raise ValueError(
                "Fingerprint verification failed. The remote instance may have changed its identity since you last connected."
            )

        if not self._verify_identity_signature(
            public_key_b64=original_identity.ed25519_public_key,
            signature_hex=response.signature,
            parsed_identity=response.identity,
            raw_identity_payload=raw_identity_payload,
        ):
            raise ValueError(
                "Fingerprint verification failed. The remote instance may have changed its identity since you last connected."
            )

        logger.info("Successfully verified remote instance identity.")

    def handle_connection_request(self, db: Session, request_data: dict) -> dict:
        """
        Handle an incoming connection request from a remote instance.

        If a valid connection_code is provided, the connection is automatically
        TRUSTED (the code proves the remote admin authorized it). Without a code,
        the connection is created as PENDING for manual review.
        """
        # Validate request data with Pydantic model
        request = RemoteConnectionRequest.model_validate(request_data)
        identity = request.identity
        signature_hex = request.signature
        connection_code = request.connection_code

        from app.services.instance_config_service import instance_config_service

        # 1. Verify the connection code if provided.
        # A valid code means the remote admin explicitly authorized this connection.
        connection_code_valid = False
        if connection_code:
            from app.utils.remote_auth import remote_auth

            current_code = remote_auth.get_code()
            if connection_code != current_code:
                raise ValueError("Invalid or expired connection code.")
            connection_code_valid = True

        # 2. Verify the Ed25519 signature to prevent spoofing.
        # Accept raw JSON and normalized model payload signing styles.
        raw_identity_payload = request_data.get("identity")
        if not self._verify_identity_signature(
            public_key_b64=identity.ed25519_public_key,
            signature_hex=signature_hex,
            parsed_identity=identity,
            raw_identity_payload=(
                raw_identity_payload if isinstance(raw_identity_payload, dict) else None
            ),
        ):
            raise ValueError("Signature verification failed for connection request.")

        # 3. Create or update the connection.
        # Auto-trust when a valid code was provided (code = explicit invitation).
        # Fall back to PENDING for unauthenticated requests (manual review required).
        fingerprint = identity.fingerprint
        conn = self.get_connection_by_fingerprint(db, fingerprint)
        remote_mode = identity.transfer_mode or TransferMode.PUSH_ONLY
        new_trust_status = TrustStatus.TRUSTED if connection_code_valid else TrustStatus.PENDING

        if not conn:
            conn = RemoteConnection(
                name=identity.instance_name,
                url=str(identity.url),
                remote_fingerprint=fingerprint,
                remote_ed25519_public_key=identity.ed25519_public_key,
                remote_x25519_public_key=identity.x25519_public_key,
                trust_status=new_trust_status,
                remote_transfer_mode=remote_mode,
            )
            db.add(conn)
        else:
            # Update connection info. Upgrade trust if code is valid or if previously rejected.
            conn.name = identity.instance_name
            conn.url = str(identity.url)
            conn.remote_transfer_mode = remote_mode
            if connection_code_valid or conn.trust_status == TrustStatus.REJECTED:
                conn.trust_status = new_trust_status

        db.commit()

        logger.info(
            "Connection from %s (%s) created/updated with status %s",
            identity.instance_name,
            fingerprint[:16],
            conn.trust_status.value,
        )

        # 4. Return our own signed identity so the initiator can verify us.
        # Instance URL may not be configured — use a placeholder so the response
        # is still valid (the initiator already knows our URL; they sent the request to us).
        instance_url = instance_config_service.get_instance_url(db)
        if not instance_url:
            logger.warning(
                "Instance URL not configured — returning placeholder in connection response. "
                "Set FF_INSTANCE_URL or configure it in the UI."
            )
            instance_url = "http://localhost"

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

        return {
            "identity": my_identity_payload,
            "signature": my_signature.hex(),
            "trust_status": conn.trust_status.value,
        }

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
                url = f"{self._base_url(conn.url)}/api/v1/remote/terminate-connection"
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

        url = f"{self._base_url(conn.url)}/api/v1/remote/sync-transfer-mode"
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
