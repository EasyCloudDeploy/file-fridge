# ruff: noqa: A003
import base64
import hashlib
import logging
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import InstanceMetadata, encryption_manager

logger = logging.getLogger(__name__)

# Constants for serialization
PUBLIC_KEY_ENCODING = serialization.Encoding.Raw
PUBLIC_KEY_FORMAT = serialization.PublicFormat.Raw
PRIVATE_KEY_ENCODING = serialization.Encoding.Raw
PRIVATE_KEY_FORMAT = serialization.PrivateFormat.Raw
PRIVATE_KEY_ENCRYPTION = serialization.NoEncryption()  # Encryption is handled by our manager


class IdentityService:
    """Manages the cryptographic identity of this File Fridge instance."""

    _instance_metadata: InstanceMetadata | None = None
    _signing_private_key: ed25519.Ed25519PrivateKey | None = None
    _kx_private_key: x25519.X25519PrivateKey | None = None

    def _load_or_create_identity(self, db: Session) -> InstanceMetadata:
        """
        Load the instance metadata from the database, creating it if it doesn't exist.
        This includes generating and storing key pairs if they are missing.
        """
        if self._instance_metadata:
            return self._instance_metadata

        metadata = db.query(InstanceMetadata).first()
        if not metadata:
            logger.info("No instance metadata found, creating a new identity.")
            metadata = InstanceMetadata(instance_uuid=str(uuid.uuid4()))
            db.add(metadata)
            # We must commit here to get an ID before key generation can proceed.
            db.commit()
            db.refresh(metadata)
            self._generate_and_save_keys(db, metadata)
        elif not all(
            [
                metadata.ed25519_public_key,
                metadata.ed25519_private_key_encrypted,
                metadata.x25519_public_key,
                metadata.x25519_private_key_encrypted,
            ]
        ):
            logger.warning("Instance identity is incomplete, regenerating keys.")
            self._generate_and_save_keys(db, metadata)

        self._instance_metadata = metadata
        return metadata

    def _generate_and_save_keys(self, db: Session, metadata: InstanceMetadata):
        """Generate and save new Ed25519 and X25519 key pairs."""
        # Generate Ed25519 (signing) key pair
        signing_private_key = ed25519.Ed25519PrivateKey.generate()
        signing_public_key = signing_private_key.public_key()

        # Generate X25519 (key exchange) key pair
        kx_private_key = x25519.X25519PrivateKey.generate()
        kx_public_key = kx_private_key.public_key()

        # Serialize private keys to bytes
        signing_priv_bytes = signing_private_key.private_bytes(
            encoding=PRIVATE_KEY_ENCODING,
            format=PRIVATE_KEY_FORMAT,
            encryption_algorithm=PRIVATE_KEY_ENCRYPTION,
        )
        kx_priv_bytes = kx_private_key.private_bytes(
            encoding=PRIVATE_KEY_ENCODING,
            format=PRIVATE_KEY_FORMAT,
            encryption_algorithm=PRIVATE_KEY_ENCRYPTION,
        )

        # Encrypt private keys
        metadata.ed25519_private_key_encrypted = encryption_manager.encrypt(
            base64.b64encode(signing_priv_bytes).decode("ascii")
        )
        metadata.x25519_private_key_encrypted = encryption_manager.encrypt(
            base64.b64encode(kx_priv_bytes).decode("ascii")
        )

        # Serialize and store public keys
        metadata.ed25519_public_key = base64.b64encode(
            signing_public_key.public_bytes(
                encoding=PUBLIC_KEY_ENCODING, format=PUBLIC_KEY_FORMAT
            )
        ).decode("ascii")
        metadata.x25519_public_key = base64.b64encode(
            kx_public_key.public_bytes(encoding=PUBLIC_KEY_ENCODING, format=PUBLIC_KEY_FORMAT)
        ).decode("ascii")

        db.commit()
        db.refresh(metadata)
        logger.info("Successfully generated and saved new instance key pairs.")

        # Clear cached private keys
        self._signing_private_key = None
        self._kx_private_key = None

    def get_instance_fingerprint(self, db: Session) -> str:
        """
        Return the SHA256 fingerprint of the instance's public signing key.
        This serves as a verifiable identifier for the instance.
        """
        metadata = self._load_or_create_identity(db)
        public_key_b64 = metadata.ed25519_public_key
        if not public_key_b64:
            # This should not happen due to the logic in _load_or_create_identity
            msg = "Public key not found for fingerprint generation."
            raise ValueError(msg)
        return hashlib.sha256(public_key_b64.encode("ascii")).hexdigest()

    def get_signing_public_key_str(self, db: Session) -> str:
        """Return the base64-encoded public signing key as a string."""
        metadata = self._load_or_create_identity(db)
        return metadata.ed25519_public_key

    def get_kx_public_key_str(self, db: Session) -> str:
        """Return the base64-encoded public key exchange key as a string."""
        metadata = self._load_or_create_identity(db)
        return metadata.x25519_public_key

    def get_signing_private_key(self, db: Session) -> ed25519.Ed25519PrivateKey:
        """Return the decrypted private signing key."""
        if self._signing_private_key:
            return self._signing_private_key

        metadata = self._load_or_create_identity(db)
        encrypted_key_b64 = metadata.ed25519_private_key_encrypted
        if not encrypted_key_b64:
            msg = "Private signing key not found in database."
            raise ValueError(msg)

        decrypted_key_b64 = encryption_manager.decrypt(encrypted_key_b64)
        key_bytes = base64.b64decode(decrypted_key_b64)
        self._signing_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
        return self._signing_private_key

    def get_kx_private_key(self, db: Session) -> x25519.X25519PrivateKey:
        """Return the decrypted private key exchange key."""
        if self._kx_private_key:
            return self._kx_private_key

        metadata = self._load_or_create_identity(db)
        encrypted_key_b64 = metadata.x25519_private_key_encrypted
        if not encrypted_key_b64:
            msg = "Private key exchange key not found in database."
            raise ValueError(msg)

        decrypted_key_b64 = encryption_manager.decrypt(encrypted_key_b64)
        key_bytes = base64.b64decode(decrypted_key_b64)
        self._kx_private_key = x25519.X25519PrivateKey.from_private_bytes(key_bytes)
        return self._kx_private_key

    def sign_message(self, db: Session, message: bytes) -> bytes:
        """Sign a message with the instance's private signing key."""
        private_key = self.get_signing_private_key(db)
        return private_key.sign(message)

    @staticmethod
    def verify_signature(public_key_b64: str, signature: bytes, message: bytes) -> bool:
        """Verify a signature using a public key."""
        try:
            key_bytes = base64.b64decode(public_key_b64)
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key.verify(signature, message)
            return True
        except Exception:
            logger.debug("Signature verification failed.", exc_info=True)
            return False


identity_service = IdentityService()


def get_identity_service() -> IdentityService:
    """Dependency injector for the IdentityService."""
    return identity_service
