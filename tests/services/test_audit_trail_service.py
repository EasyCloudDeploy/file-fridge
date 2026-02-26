import pytest
from pathlib import Path
from datetime import datetime, timezone

from app.models import FileTransactionHistory, TransactionType, FileStatus, StorageType
from app.services.audit_trail_service import audit_trail_service


@pytest.mark.unit
class TestAuditTrailService:
    def test_log_transaction_success(self, db_session, file_inventory_factory):
        """Test logging a generic file transaction."""
        inv = file_inventory_factory(path="/tmp/tx.txt")
        
        tx = audit_trail_service.log_transaction(
            db_session, 
            file=inv, 
            transaction_type=TransactionType.FREEZE,
            initiated_by="test_user",
            operation_metadata={"key": "value"}
        )
        
        assert tx.id is not None
        assert tx.file_id == inv.id
        assert tx.transaction_type == TransactionType.FREEZE
        assert tx.initiated_by == "test_user"
        assert "value" in tx.operation_metadata

    def test_log_freeze_operation(self, db_session, file_inventory_factory):
        """Test the freeze convenience method."""
        inv = file_inventory_factory(path="/tmp/hot/f.txt")
        src = Path("/tmp/hot/f.txt")
        dst = Path("/tmp/cold/f.txt")
        
        tx = audit_trail_service.log_freeze_operation(
            db_session, inv, src, dst, storage_location_id=5
        )
        
        assert tx.transaction_type == TransactionType.FREEZE
        assert tx.old_path == str(src)
        assert tx.new_path == str(dst)
        assert tx.new_storage_location_id == 5

    def test_log_thaw_operation(self, db_session, file_inventory_factory):
        """Test the thaw convenience method."""
        inv = file_inventory_factory(path="/tmp/cold/f.txt", storage_type=StorageType.COLD)
        src = Path("/tmp/cold/f.txt")
        dst = Path("/tmp/hot/f.txt")
        
        tx = audit_trail_service.log_thaw_operation(
            db_session, inv, src, dst
        )
        
        assert tx.transaction_type == TransactionType.THAW
        assert tx.old_storage_type == StorageType.COLD
        assert tx.new_storage_type == StorageType.HOT

    def test_log_remote_migration(self, db_session, file_inventory_factory):
        """Test the remote migration convenience method."""
        inv = file_inventory_factory()
        tx = audit_trail_service.log_remote_migration(
            db_session, inv, "http://remote-server"
        )
        assert tx.transaction_type == TransactionType.REMOTE_MIGRATE
        assert "remote-server" in tx.operation_metadata

    def test_log_status_change(self, db_session, file_inventory_factory):
        """Test logging status changes."""
        inv = file_inventory_factory(status=FileStatus.ACTIVE)
        tx = audit_trail_service.log_status_change(
            db_session, inv, FileStatus.ACTIVE, FileStatus.MISSING, reason="Not found"
        )
        assert tx.old_status == FileStatus.ACTIVE
        assert tx.new_status == FileStatus.MISSING
        assert "Not found" in tx.operation_metadata

    def test_get_file_history(self, db_session, file_inventory_factory):
        """Test retrieving history for a file."""
        inv = file_inventory_factory()
        # Add 2 entries with a gap larger than 1s for stable timestamp-based ordering in SQLite
        audit_trail_service.log_transaction(db_session, inv, TransactionType.FREEZE)
        import time
        time.sleep(1.1)
        audit_trail_service.log_transaction(db_session, inv, TransactionType.THAW)
        
        history = audit_trail_service.get_file_history(db_session, inv.id)
        assert len(history) == 2
        # Ordered by newest first
        assert history[0].transaction_type == TransactionType.THAW

    def test_get_failed_transactions(self, db_session, file_inventory_factory):
        """Test retrieving only failed transactions."""
        inv = file_inventory_factory()
        # Success
        audit_trail_service.log_transaction(db_session, inv, TransactionType.FREEZE, success=True)
        # Failure
        audit_trail_service.log_transaction(db_session, inv, TransactionType.FREEZE, success=False, error_message="Disk full")
        
        failed = audit_trail_service.get_failed_transactions(db_session)
        assert len(failed) == 1
        assert failed[0].error_message == "Disk full"
