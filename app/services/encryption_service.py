import base64
import logging
import os
from pathlib import Path
from typing import Optional


from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from app.models import encryption_manager
from app.services.identity_service import identity_service

logger = logging.getLogger(__name__)


class FileEncryptionService:
    """
    Service for encrypting and decrypting files in cold storage.
    Uses AES-256-GCM with keys derived from the instance's identity keys.
    """

    # Constants
    CHUNK_SIZE = 64 * 1024  # 64KB chunks
    NONCE_SIZE = 12  # 96 bits for GCM
    TAG_SIZE = 16  # 128 bits for GCM
    SALT_SIZE = 16

    def _get_or_create_root_key(self, db: Session) -> bytes:
        """
        Get or create the persistent file encryption root key.
        This key is independent of the instance identity to allow key rotation/restore without data loss.
        Uses row-level locking to prevent concurrent key generation.
        """
        # First ensure the InstanceMetadata row exists
        identity_service._load_or_create_identity(db)

        # Re-query with FOR UPDATE lock to prevent concurrent key generation
        from app.models import InstanceMetadata

        metadata = db.query(InstanceMetadata).with_for_update().first()

        if metadata.file_encryption_root_key_encrypted:
            # Decrypt existing key
            try:
                key_b64 = encryption_manager.decrypt(metadata.file_encryption_root_key_encrypted)
                return base64.b64decode(key_b64)
            except Exception:
                logger.exception("Failed to decrypt file encryption root key")
                # Fallback? No, this is critical. If we can't decrypt the root key, we can't access files.
                # However, for robustness during migration/dev, we might need a strategy.
                raise

        # Generate new random 32-byte key (only one worker will reach here due to the lock)
        new_key = os.urandom(32)
        key_b64 = base64.b64encode(new_key).decode("ascii")

        # Encrypt and save
        metadata.file_encryption_root_key_encrypted = encryption_manager.encrypt(key_b64)
        db.commit()
        db.refresh(metadata)

        logger.info("Generated new persistent file encryption root key")
        return new_key

    def _get_previous_root_key(self, db: Session) -> Optional[bytes]:
        """
        Get the previous file encryption root key if a migration is in progress.
        """
        from app.models import InstanceMetadata
        metadata = db.query(InstanceMetadata).first()
        if metadata and metadata.previous_file_encryption_root_key_encrypted:
            try:
                key_b64 = encryption_manager.decrypt(metadata.previous_file_encryption_root_key_encrypted)
                return base64.b64decode(key_b64)
            except Exception:
                logger.exception("Failed to decrypt previous file encryption root key")
        return None

    def _derive_key_from_root(self, root_key: bytes, salt: bytes) -> bytes:
        """
        Derive a symmetric encryption key from a given root key.
        """
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,  # 256 bits for AES-256
            salt=salt,
            info=b"file-fridge-cold-storage-encryption",
        )
        return hkdf.derive(root_key)

    def _derive_key(self, db: Session, salt: bytes) -> bytes:
        """
        Derive a symmetric encryption key from the persistent root key.
        """
        root_key = self._get_or_create_root_key(db)
        return self._derive_key_from_root(root_key, salt)

    def encrypt_file(self, db: Session, input_path: Path, output_path: Path) -> None:
        """
        Encrypt a file using AES-256-GCM.
        Format: [SALT (16)][NONCE (12)][CIPHERTEXT...][TAG (16)]
        """
        try:
            salt = os.urandom(self.SALT_SIZE)
            nonce = os.urandom(self.NONCE_SIZE)
            key = self._derive_key(db, salt)

            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
            encryptor = cipher.encryptor()

            with input_path.open("rb") as f_in, output_path.open("wb") as f_out:
                # Write header: Salt + Nonce
                f_out.write(salt)
                f_out.write(nonce)

                while True:
                    chunk = f_in.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    ciphertext = encryptor.update(chunk)
                    f_out.write(ciphertext)

                f_out.write(encryptor.finalize())
                f_out.write(encryptor.tag)

            logger.debug(f"Encrypted file: {input_path} -> {output_path}")

        except Exception as e:
            logger.exception(f"Failed to encrypt file {input_path}")
            if output_path.exists():
                output_path.unlink()
            raise e

    def _decrypt_file_with_root_key(self, root_key: bytes, input_path: Path, output_path: Path) -> None:
        """
        Helper method to decrypt a file using a concrete root key.
        """
        file_size = input_path.stat().st_size
        if file_size < (self.SALT_SIZE + self.NONCE_SIZE + self.TAG_SIZE):
            msg = "File too small to be a valid encrypted file"
            raise ValueError(msg)

        with input_path.open("rb") as f_in:
            salt = f_in.read(self.SALT_SIZE)
            nonce = f_in.read(self.NONCE_SIZE)

            key = self._derive_key_from_root(root_key, salt)

            # Seek to end, read tag, seek back to ciphertext
            f_in.seek(-self.TAG_SIZE, 2)
            tag = f_in.read(self.TAG_SIZE)
            f_in.seek(self.SALT_SIZE + self.NONCE_SIZE, 0)

            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
            decryptor = cipher.decryptor()

            with output_path.open("wb") as f_out:
                ciphertext_len = file_size - self.SALT_SIZE - self.NONCE_SIZE - self.TAG_SIZE
                bytes_read = 0

                while bytes_read < ciphertext_len:
                    chunk_size = min(self.CHUNK_SIZE, ciphertext_len - bytes_read)
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break

                    plaintext = decryptor.update(chunk)
                    f_out.write(plaintext)
                    bytes_read += len(chunk)

                f_out.write(decryptor.finalize())

    def decrypt_file(self, db: Session, input_path: Path, output_path: Path) -> None:
        """
        Decrypt a file using AES-256-GCM.
        Falls back to previous root key if active root key decryption fails and previous is available.
        """
        active_root_key = self._get_or_create_root_key(db)
        try:
            self._decrypt_file_with_root_key(active_root_key, input_path, output_path)
            logger.debug(f"Decrypted file using active root key: {input_path} -> {output_path}")
            return
        except Exception as active_err:
            prev_root_key = self._get_previous_root_key(db)
            if prev_root_key:
                try:
                    logger.info(f"Decrypting with active root key failed, attempting fallback to previous key: {input_path}")
                    self._decrypt_file_with_root_key(prev_root_key, input_path, output_path)
                    logger.debug(f"Decrypted file using previous root key: {input_path} -> {output_path}")
                    return
                except Exception:
                    pass
            # If fallback also fails, clean up output file and raise the active_err
            if output_path.exists():
                output_path.unlink()
            raise active_err


file_encryption_service = FileEncryptionService()
