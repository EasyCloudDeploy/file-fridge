import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

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

    def _derive_key(self, db: Session, salt: bytes) -> bytes:
        """
        Derive a symmetric encryption key from the instance's private key.
        We use the X25519 private key bytes as the input keying material (IKM).
        """
        # Get the raw private key bytes
        # Note: We use the raw bytes of the private key, not the PEM format
        kx_private = identity_service.get_kx_private_key(db)

        from cryptography.hazmat.primitives import serialization

        key_bytes = kx_private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,  # 256 bits for AES-256
            salt=salt,
            info=b"file-fridge-cold-storage-encryption",
        )
        return hkdf.derive(key_bytes)

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

            with open(input_path, "rb") as f_in, open(output_path, "wb") as f_out:
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
            logger.error(f"Failed to encrypt file {input_path}: {e}")
            if output_path.exists():
                output_path.unlink()
            raise

    def decrypt_file(self, db: Session, input_path: Path, output_path: Path) -> None:
        """
        Decrypt a file using AES-256-GCM.
        Expects format: [SALT (16)][NONCE (12)][CIPHERTEXT...][TAG (16)]

        Note: AES-GCM decryption technically requires verification of the tag BEFORE release of plaintext.
        However, for large files, buffering everything in memory is not feasible.
        Standard cryptography libraries often verify only at finalize().

        SECURITY NOTE: If we stream the plaintext out before finalize(), and the tag check fails,
        the caller (and user) might have already processed invalid/malicious plaintext.

        In this implementation, we write to a temporary output file. If finalize() fails (tag mismatch),
        we delete the output file and raise an error. This prevents 'releasing' the full file,
        though an attacker watching the disk could technically see chunks.
        For a rigorous security model, we should decrypt to a temp location (which we do)
        and only rename/move it after successful verification.
        """
        try:
            file_size = input_path.stat().st_size
            if file_size < (self.SALT_SIZE + self.NONCE_SIZE + self.TAG_SIZE):
                raise ValueError("File too small to be a valid encrypted file")

            with open(input_path, "rb") as f_in:
                salt = f_in.read(self.SALT_SIZE)
                nonce = f_in.read(self.NONCE_SIZE)

                key = self._derive_key(db, salt)

                # We need to handle the tag separately. GCM requires tag passed to constructor for decryption,
                # or set before finalize. The python cryptography library takes it in decryptor.finalize()
                # OR as a parameter to GCM(nonce, tag).
                # But we don't know the tag until we read the end of the file.

                # Option A: Seek to end, read tag, seek back.
                f_in.seek(-self.TAG_SIZE, 2)  # Seek from end
                tag = f_in.read(self.TAG_SIZE)
                f_in.seek(self.SALT_SIZE + self.NONCE_SIZE, 0)  # Seek back to start of ciphertext

                cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
                decryptor = cipher.decryptor()

                with open(output_path, "wb") as f_out:
                    # Calculate how much ciphertext to read (Total - Salt - Nonce - Tag)
                    ciphertext_len = file_size - self.SALT_SIZE - self.NONCE_SIZE - self.TAG_SIZE
                    bytes_read = 0

                    while bytes_read < ciphertext_len:
                        chunk_size = min(self.CHUNK_SIZE, ciphertext_len - bytes_read)
                        chunk = f_in.read(chunk_size)
                        if not chunk:
                            break  # Should not happen based on size check

                        plaintext = decryptor.update(chunk)
                        f_out.write(plaintext)
                        bytes_read += len(chunk)

                    # Finalize verifies the tag
                    f_out.write(decryptor.finalize())

            logger.debug(f"Decrypted file: {input_path} -> {output_path}")

        except Exception as e:
            logger.error(f"Failed to decrypt file {input_path}: {e}")
            if output_path.exists():
                output_path.unlink()
            raise


file_encryption_service = FileEncryptionService()
