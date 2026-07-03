import hashlib
from typing import Annotated, Any, List

from cryptography.fernet import Fernet
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notifier, ServerEncryptionKey, encryption_manager
from app.routers.api.auth import get_current_user
from app.schemas import ServerEncryptionKeyResponse

router = APIRouter(prefix="/api/v1/encryption", tags=["Encryption"])


@router.get("/keys", response_model=List[ServerEncryptionKeyResponse])
def list_keys(
    db: Annotated[Session, Depends(get_db)], current_user: Annotated[Any, Depends(get_current_user)]
):
    """List all server encryption keys."""
    return db.query(ServerEncryptionKey).order_by(ServerEncryptionKey.created_at.desc()).all()


def re_encrypt_all_db_settings(db: Session):
    """
    Decrypts all database settings using the MultiFernet manager (falling back to old keys if needed)
    and re-encrypts them under the newly generated active key.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Import models locally to avoid circular dependencies
    from app.models import (
        Notifier,
        InstanceMetadata,
        ColdStorageLocation,
        P2PNetworkConfig,
        InstanceKeyHistory,
        encryption_manager,
    )
    from cryptography.fernet import InvalidToken

    # Helper to decrypt safely and check if we actually decrypted something using the MultiFernet
    def re_encrypt_value(val: str) -> str:
        if not val:
            return val
        try:
            # We bypass the default manager.decrypt fallback return value (which returns input as-is on failure)
            # by directly calling decrypt on the multi_cipher and catching the InvalidToken exception.
            decrypted = encryption_manager._get_cipher().decrypt(val.encode()).decode()
            return encryption_manager.encrypt(decrypted)
        except InvalidToken:
            # Decryption failed; likely already unencrypted or encrypted with a key we don't have
            return val
        except Exception as e:
            logger.error(f"Error re-encrypting setting: {e}")
            return val

    try:
        # 1. Notifiers
        for notifier in db.query(Notifier).all():
            if notifier.smtp_password_encrypted:
                new_val = re_encrypt_value(notifier.smtp_password_encrypted)
                if new_val != notifier.smtp_password_encrypted:
                    notifier.smtp_password_encrypted = new_val

        # 2. Instance Metadata
        for metadata in db.query(InstanceMetadata).all():
            if metadata.ed25519_private_key_encrypted:
                metadata.ed25519_private_key_encrypted = re_encrypt_value(metadata.ed25519_private_key_encrypted)
            if metadata.x25519_private_key_encrypted:
                metadata.x25519_private_key_encrypted = re_encrypt_value(metadata.x25519_private_key_encrypted)
            if metadata.file_encryption_root_key_encrypted:
                metadata.file_encryption_root_key_encrypted = re_encrypt_value(metadata.file_encryption_root_key_encrypted)
            if metadata.smtp_password_encrypted:
                metadata.smtp_password_encrypted = re_encrypt_value(metadata.smtp_password_encrypted)

        # 3. Cold Storage Locations
        for loc in db.query(ColdStorageLocation).all():
            if loc.backend_config_encrypted:
                new_val = re_encrypt_value(loc.backend_config_encrypted)
                if new_val != loc.backend_config_encrypted:
                    loc.backend_config_encrypted = new_val

        # 4. P2P Network Config
        for p2p in db.query(P2PNetworkConfig).all():
            if p2p.psk_encrypted:
                new_val = re_encrypt_value(p2p.psk_encrypted)
                if new_val != p2p.psk_encrypted:
                    p2p.psk_encrypted = new_val

        # 5. Instance Key History
        for hist in db.query(InstanceKeyHistory).all():
            if hist.ed25519_private_key_encrypted:
                hist.ed25519_private_key_encrypted = re_encrypt_value(hist.ed25519_private_key_encrypted)
            if hist.x25519_private_key_encrypted:
                hist.x25519_private_key_encrypted = re_encrypt_value(hist.x25519_private_key_encrypted)

        db.commit()
        logger.info("Successfully re-encrypted all database settings with the latest server encryption key.")
    except Exception as exc:
        db.rollback()
        logger.exception(f"Failed to re-encrypt database settings: {exc}")


@router.post("/keys", response_model=ServerEncryptionKeyResponse)
def generate_key(
    db: Annotated[Session, Depends(get_db)], current_user: Annotated[Any, Depends(get_current_user)]
):
    """Generate a new encryption key (rotate)."""
    new_key = Fernet.generate_key().decode()
    fingerprint = hashlib.sha256(new_key.encode()).hexdigest()

    db_key = ServerEncryptionKey(key_value=new_key, fingerprint=fingerprint)
    db.add(db_key)
    db.commit()
    db.refresh(db_key)

    # Reset encryption manager to load the new key
    encryption_manager.reset()

    # Re-encrypt all database settings with the new key
    re_encrypt_all_db_settings(db)

    return db_key


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(
    key_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Any, Depends(get_current_user)],
):
    """Delete an encryption key."""
    key = db.query(ServerEncryptionKey).filter(ServerEncryptionKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    # Check if this is the last key
    total_keys = db.query(ServerEncryptionKey).count()
    if total_keys <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the last encryption key. Generate a new one first.",
        )

    # Optional: Check if any data is encrypted with this key
    # We'll implement a safety check: clear fields that were encrypted WITH THIS KEY
    notifiers = db.query(Notifier).all()
    for notifier in notifiers:
        if notifier.smtp_password_encrypted:
            # If this key can decrypt it, and NO OTHER REMAINING key can decrypt it,
            # we should probably warn or handle it.
            # Simplified: if this key can decrypt it, we clear it to be safe.
            if encryption_manager.can_decrypt_with_key(
                notifier.smtp_password_encrypted, key.key_value
            ):
                notifier.smtp_password = None

    db.delete(key)
    db.commit()

    # Reset encryption manager
    encryption_manager.reset()


def run_file_key_migration(metadata_id: int):
    """
    Background task to migrate all encrypted files in cold storage to the new root key.
    """
    import logging
    logger = logging.getLogger(__name__)

    from app.database import SessionLocal
    from app.models import InstanceMetadata, FileInventory, StorageType, FileRecord, OperationType
    from app.services.encryption_service import file_encryption_service
    from app.services.cold_storage_backends import get_backend
    import tempfile
    from pathlib import Path

    db = SessionLocal()
    try:
        metadata = db.query(InstanceMetadata).filter(InstanceMetadata.id == metadata_id).first()
        if not metadata or not metadata.previous_file_encryption_root_key_encrypted:
            logger.error("No migration in progress or metadata not found for key migration.")
            return

        # Gather all files in cold storage that are encrypted
        files = db.query(FileInventory).filter(
            FileInventory.storage_type == StorageType.COLD,
            FileInventory.is_encrypted == True
        ).all()

        metadata.file_migration_total = len(files)
        metadata.file_migration_progress = 0
        db.commit()

        success_count = 0
        for idx, file in enumerate(files):
            try:
                if not file.storage_location:
                    metadata.file_migration_progress = idx + 1
                    db.commit()
                    continue

                backend = get_backend(file.storage_location)
                is_local = backend.backend_name() == "local"

                if is_local:
                    source_path = Path(file.file_path)
                    if not source_path.exists():
                        logger.warning(f"Local file missing during key migration: {source_path}")
                        metadata.file_migration_progress = idx + 1
                        db.commit()
                        continue

                    # Decrypt with old key, encrypt with new key using temp files
                    with tempfile.NamedTemporaryFile(prefix="ff-mig-dec-", delete=False) as dec_tmp:
                        dec_path = Path(dec_tmp.name)
                    with tempfile.NamedTemporaryFile(prefix="ff-mig-enc-", suffix=".ffenc", delete=False) as enc_tmp:
                        enc_path = Path(enc_tmp.name)

                    try:
                        # Try decrypting using the previous root key
                        file_encryption_service._decrypt_file_with_root_key(
                            file_encryption_service._get_previous_root_key(db),
                            source_path,
                            dec_path
                        )
                        # Encrypt using the active root key
                        file_encryption_service.encrypt_file(db, dec_path, enc_path)
                        # Overwrite active path atomically
                        enc_path.replace(source_path)
                        success_count += 1
                    except Exception as e:
                        # If decryption fails, check if already migrated (can decrypt with active key)
                        try:
                            file_encryption_service._decrypt_file_with_root_key(
                                file_encryption_service._get_or_create_root_key(db),
                                source_path,
                                dec_path
                            )
                            # Yes! Already migrated.
                            success_count += 1
                        except Exception:
                            logger.error(f"Failed to migrate key for local file {file.id}: {e}")
                    finally:
                        if dec_path.exists():
                            dec_path.unlink()
                        if enc_path.exists():
                            enc_path.unlink()
                else:
                    # Remote backend: download, decrypt, encrypt, upload, replace reference
                    storage_reference = file.file_path
                    with tempfile.NamedTemporaryFile(prefix="ff-mig-dl-", delete=False) as dl_tmp:
                        dl_path = Path(dl_tmp.name)
                    with tempfile.NamedTemporaryFile(prefix="ff-mig-dec-", delete=False) as dec_path_tmp:
                        dec_path = Path(dec_path_tmp.name)
                    with tempfile.NamedTemporaryFile(prefix="ff-mig-enc-", suffix=".ffenc", delete=False) as enc_path_tmp:
                        enc_path = Path(enc_path_tmp.name)

                    try:
                        dl_success, dl_error = backend.download_file(storage_reference, dl_path, file.storage_location)
                        if not dl_success:
                            logger.error(f"Failed to download file {file.id} for key migration: {dl_error}")
                            metadata.file_migration_progress = idx + 1
                            db.commit()
                            continue

                        # Try decrypting using the previous root key
                        file_encryption_service._decrypt_file_with_root_key(
                            file_encryption_service._get_previous_root_key(db),
                            dl_path,
                            dec_path
                        )
                        # Encrypt using the active root key
                        file_encryption_service.encrypt_file(db, dec_path, enc_path)

                        # Find relative path in cold storage
                        file_record = db.query(FileRecord).filter(FileRecord.cold_storage_path == storage_reference).first()
                        if file_record:
                            name = storage_reference.rstrip("/").split("/")[-1]
                            relative_path = Path(name)
                        else:
                            name = storage_reference.rstrip("/").split("/")[-1]
                            relative_path = Path(name)

                        # Upload to backend
                        ul_success, ul_error, new_reference, _ = backend.freeze_file(
                            source_path=enc_path,
                            relative_path=relative_path,
                            location=file.storage_location,
                            operation_mode=OperationType.COPY,
                        )
                        if not ul_success:
                            logger.error(f"Failed to upload migrated file {file.id}: {ul_error}")
                            metadata.file_migration_progress = idx + 1
                            db.commit()
                            continue

                        # Delete old remote reference
                        backend.delete(storage_reference, file.storage_location)

                        # Update database reference
                        file.file_path = new_reference
                        if file_record:
                            file_record.cold_storage_path = new_reference
                        db.commit()
                        success_count += 1
                    except Exception as e:
                        # If decryption fails, check if already migrated
                        try:
                            file_encryption_service._decrypt_file_with_root_key(
                                file_encryption_service._get_or_create_root_key(db),
                                dl_path,
                                dec_path
                            )
                            success_count += 1
                        except Exception:
                            logger.error(f"Failed to migrate key for remote file {file.id}: {e}")
                    finally:
                        if dl_path.exists():
                            dl_path.unlink()
                        if dec_path.exists():
                            dec_path.unlink()
                        if enc_path.exists():
                            enc_path.unlink()

                metadata.file_migration_progress = idx + 1
                db.commit()
            except Exception as e:
                logger.error(f"Error migrating file record {file.id}: {e}")
                metadata.file_migration_progress = idx + 1
                db.commit()

        # Complete migration
        metadata.previous_file_encryption_root_key_encrypted = None
        metadata.file_migration_total = None
        metadata.file_migration_progress = None
        db.commit()
        logger.info(f"File key migration completed. Migrated {success_count}/{len(files)} files successfully.")

    except Exception as exc:
        logger.exception(f"Fatal error in run_file_key_migration: {exc}")
    finally:
        db.close()


@router.post("/keys/rotate-file-key")
def rotate_file_key(
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Any, Depends(get_current_user)]
):
    """Rotate the cold storage file encryption root key and start background file migration."""
    from app.models import InstanceMetadata, encryption_manager
    import os
    import base64

    metadata = db.query(InstanceMetadata).first()
    if not metadata:
        raise HTTPException(status_code=404, detail="Instance metadata not found")

    if metadata.previous_file_encryption_root_key_encrypted is not None:
        raise HTTPException(status_code=400, detail="File encryption key migration is already in progress.")

    # Generate new 32-byte root key
    new_root_key = os.urandom(32)
    new_root_key_b64 = base64.b64encode(new_root_key).decode("ascii")

    # Get existing root key to store as previous (if exists)
    if metadata.file_encryption_root_key_encrypted:
        metadata.previous_file_encryption_root_key_encrypted = metadata.file_encryption_root_key_encrypted
    
    # Encrypt and set new root key
    metadata.file_encryption_root_key_encrypted = encryption_manager.encrypt(new_root_key_b64)
    db.commit()

    # Trigger background task
    background_tasks.add_task(run_file_key_migration, metadata.id)

    return {"detail": "File encryption root key rotated. Migration task started in the background."}


@router.get("/keys/migration-status")
def get_migration_status(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Any, Depends(get_current_user)]
):
    """Get the current file encryption key migration status."""
    from app.models import InstanceMetadata

    metadata = db.query(InstanceMetadata).first()
    if not metadata:
        raise HTTPException(status_code=404, detail="Instance metadata not found")

    in_progress = metadata.previous_file_encryption_root_key_encrypted is not None
    return {
        "in_progress": in_progress,
        "total": metadata.file_migration_total or 0,
        "progress": metadata.file_migration_progress or 0
    }
