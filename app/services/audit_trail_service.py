"""Audit trail service - tracks all file state transitions and operations."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import (
    FileInventory,
    FileStatus,
    FileTransactionHistory,
    StorageType,
    TransactionType,
)

logger = logging.getLogger(__name__)


class AuditTrailService:
    """Service for tracking all file operations and state transitions."""

    @staticmethod
    def log_transaction(
        db: Session,
        file: FileInventory,
        transaction_type: TransactionType,
        old_storage_type: Optional[StorageType] = None,
        new_storage_type: Optional[StorageType] = None,
        old_status: Optional[FileStatus] = None,
        new_status: Optional[FileStatus] = None,
        old_path: Optional[str] = None,
        new_path: Optional[str] = None,
        old_storage_location_id: Optional[int] = None,
        new_storage_location_id: Optional[int] = None,
        checksum_before: Optional[str] = None,
        checksum_after: Optional[str] = None,
        operation_metadata: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        initiated_by: Optional[str] = None,
    ) -> FileTransactionHistory:
        """
        Log a file transaction to the audit trail.

        Args:
            db: Database session
            file: The FileInventory record this transaction applies to
            transaction_type: Type of operation performed
            old_storage_type: Previous storage tier (if changed)
            new_storage_type: New storage tier (if changed)
            old_status: Previous file status (if changed)
            new_status: New file status (if changed)
            old_path: Previous file path (if changed)
            new_path: New file path (if changed)
            old_storage_location_id: Previous cold storage location (if changed)
            new_storage_location_id: New cold storage location (if changed)
            checksum_before: Checksum before operation
            checksum_after: Checksum after operation
            operation_metadata: Additional context as dict (will be JSON serialized)
            success: Whether the operation succeeded
            error_message: Error message if operation failed
            initiated_by: User or system component that initiated the operation

        Returns:
            Created FileTransactionHistory record
        """
        try:
            transaction = FileTransactionHistory(
                file_id=file.id,
                transaction_type=transaction_type,
                old_storage_type=old_storage_type or file.storage_type,
                new_storage_type=new_storage_type or file.storage_type,
                old_status=old_status or file.status,
                new_status=new_status or file.status,
                old_path=old_path or file.file_path,
                new_path=new_path or file.file_path,
                old_storage_location_id=old_storage_location_id or file.cold_storage_location_id,
                new_storage_location_id=new_storage_location_id or file.cold_storage_location_id,
                file_size=file.file_size,
                checksum_before=checksum_before or file.checksum,
                checksum_after=checksum_after or file.checksum,
                operation_metadata=json.dumps(operation_metadata) if operation_metadata else None,
                success=success,
                error_message=error_message,
                initiated_by=initiated_by,
            )
            db.add(transaction)
            db.commit()
            db.refresh(transaction)

            logger.debug(
                f"Logged transaction {transaction.id}: {transaction_type.value} for file {file.id} "
                f"(success={success})"
            )
            return transaction

        except Exception:
            logger.exception("Failed to log audit trail entry")
            db.rollback()
            raise

    @staticmethod
    def log_freeze_operation(
        db: Session,
        file: FileInventory,
        source_path: Path,
        dest_path: Path,
        storage_location_id: int,
        checksum_before: Optional[str] = None,
        checksum_after: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        initiated_by: Optional[str] = None,
    ) -> FileTransactionHistory:
        """Convenience method to log a freeze operation (hot → cold)."""
        return AuditTrailService.log_transaction(
            db=db,
            file=file,
            transaction_type=TransactionType.FREEZE,
            old_storage_type=StorageType.HOT,
            new_storage_type=StorageType.COLD,
            old_path=str(source_path),
            new_path=str(dest_path),
            new_storage_location_id=storage_location_id,
            checksum_before=checksum_before,
            checksum_after=checksum_after,
            operation_metadata={
                "source_path": str(source_path),
                "dest_path": str(dest_path),
                "storage_location_id": storage_location_id,
            },
            success=success,
            error_message=error_message,
            initiated_by=initiated_by,
        )

    @staticmethod
    def log_thaw_operation(
        db: Session,
        file: FileInventory,
        source_path: Path,
        dest_path: Path,
        checksum_before: Optional[str] = None,
        checksum_after: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        initiated_by: Optional[str] = None,
    ) -> FileTransactionHistory:
        """Convenience method to log a thaw operation (cold → hot)."""
        return AuditTrailService.log_transaction(
            db=db,
            file=file,
            transaction_type=TransactionType.THAW,
            old_storage_type=StorageType.COLD,
            new_storage_type=StorageType.HOT,
            old_path=str(source_path),
            new_path=str(dest_path),
            checksum_before=checksum_before,
            checksum_after=checksum_after,
            operation_metadata={
                "source_path": str(source_path),
                "dest_path": str(dest_path),
            },
            success=success,
            error_message=error_message,
            initiated_by=initiated_by,
        )

    @staticmethod
    def log_remote_migration(
        db: Session,
        file: FileInventory,
        remote_url: str,
        success: bool = True,
        error_message: Optional[str] = None,
        initiated_by: Optional[str] = None,
    ) -> FileTransactionHistory:
        """Convenience method to log a remote migration."""
        return AuditTrailService.log_transaction(
            db=db,
            file=file,
            transaction_type=TransactionType.REMOTE_MIGRATE,
            operation_metadata={
                "remote_url": remote_url,
                "action": "migrated_to_remote",
            },
            success=success,
            error_message=error_message,
            initiated_by=initiated_by,
        )

    @staticmethod
    def log_status_change(
        db: Session,
        file: FileInventory,
        old_status: FileStatus,
        new_status: FileStatus,
        reason: Optional[str] = None,
        initiated_by: Optional[str] = None,
    ) -> FileTransactionHistory:
        """Convenience method to log a status change."""
        return AuditTrailService.log_transaction(
            db=db,
            file=file,
            transaction_type=TransactionType.CLEANUP,
            old_status=old_status,
            new_status=new_status,
            operation_metadata={"reason": reason} if reason else None,
            success=True,
            initiated_by=initiated_by,
        )

    @staticmethod
    def get_file_history(
        db: Session, file_id: int, limit: int = 100, include_failures: bool = True
    ) -> list[FileTransactionHistory]:
        """
        Get transaction history for a specific file.

        Args:
            db: Database session
            file_id: File inventory ID
            limit: Maximum number of records to return
            include_failures: If False, only return successful transactions

        Returns:
            List of transaction records ordered by creation time (newest first)
        """
        query = db.query(FileTransactionHistory).filter(FileTransactionHistory.file_id == file_id)

        if not include_failures:
            query = query.filter(FileTransactionHistory.success)

        return query.order_by(FileTransactionHistory.created_at.desc()).limit(limit).all()

    @staticmethod
    def get_failed_transactions(
        db: Session, limit: int = 100, since: Optional[datetime] = None
    ) -> list[FileTransactionHistory]:
        """
        Get failed transactions for investigation.

        Args:
            db: Database session
            limit: Maximum number of records to return
            since: Only return transactions after this datetime

        Returns:
            List of failed transaction records
        """
        query = db.query(FileTransactionHistory).filter(FileTransactionHistory.success == False)

        if since:
            query = query.filter(FileTransactionHistory.created_at >= since)

        return query.order_by(FileTransactionHistory.created_at.desc()).limit(limit).all()


# Singleton instance
audit_trail_service = AuditTrailService()
