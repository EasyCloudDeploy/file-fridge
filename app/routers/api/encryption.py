import hashlib
from typing import List

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notifier, ServerEncryptionKey, encryption_manager
from app.routers.api.auth import get_current_user
from app.schemas import ServerEncryptionKeyResponse

router = APIRouter(prefix="/encryption", tags=["Encryption"])

@router.get("/keys", response_model=List[ServerEncryptionKeyResponse])
def list_keys(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List all server encryption keys."""
    return db.query(ServerEncryptionKey).order_by(ServerEncryptionKey.created_at.desc()).all()

@router.post("/keys", response_model=ServerEncryptionKeyResponse)
def generate_key(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Generate a new encryption key (rotate)."""
    new_key = Fernet.generate_key().decode()
    fingerprint = hashlib.sha256(new_key.encode()).hexdigest()

    db_key = ServerEncryptionKey(
        key_value=new_key,
        fingerprint=fingerprint
    )
    db.add(db_key)
    db.commit()
    db.refresh(db_key)

    # Reset encryption manager to load the new key
    encryption_manager.reset()

    return db_key

@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
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
            detail="Cannot delete the last encryption key. Generate a new one first."
        )

    # Optional: Check if any data is encrypted with this key
    # We'll implement a safety check: clear fields that were encrypted WITH THIS KEY
    notifiers = db.query(Notifier).all()
    for notifier in notifiers:
        if notifier.smtp_password_encrypted:
            # If this key can decrypt it, and NO OTHER REMAINING key can decrypt it,
            # we should probably warn or handle it.
            # Simplified: if this key can decrypt it, we clear it to be safe.
            if encryption_manager.can_decrypt_with_key(notifier.smtp_password_encrypted, key.key_value):
                notifier.smtp_password = None

    db.delete(key)
    db.commit()

    # Reset encryption manager
    encryption_manager.reset()

