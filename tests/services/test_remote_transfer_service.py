import pytest
from unittest.mock import MagicMock, AsyncMock

from app.models import (
    RemoteTransferJob, 
    TransferStatus, 
    TransferDirection, 
    FileInventory, 
    RemoteConnection,
    MonitoredPath
)
from app.services.remote_transfer_service import remote_transfer_service


@pytest.mark.unit
class TestRemoteTransferService:
    def test_create_transfer_job_success(self, db_session, file_inventory_factory, remote_connection_factory, tmp_path):
        """Test successful creation of a transfer job."""
        source_file = tmp_path / "transfer.txt"
        source_file.write_text("content")
        
        inv = file_inventory_factory(path=str(source_file))
        conn = remote_connection_factory()
        
        job = remote_transfer_service.create_transfer_job(
            db_session, 
            file_id=inv.id, 
            remote_connection_id=conn.id, 
            remote_monitored_path_id=10
        )
        
        assert job.id is not None
        assert job.file_inventory_id == inv.id
        assert job.remote_connection_id == conn.id
        assert job.status == TransferStatus.PENDING
        assert job.direction == TransferDirection.PUSH

    def test_create_transfer_job_file_not_found(self, db_session, remote_connection_factory):
        """Test error when file is missing from inventory."""
        conn = remote_connection_factory()
        with pytest.raises(ValueError, match="not found in inventory"):
            remote_transfer_service.create_transfer_job(db_session, 9999, conn.id, 1)

    def test_create_transfer_job_conn_not_found(self, db_session, file_inventory_factory):
        """Test error when remote connection is missing."""
        inv = file_inventory_factory()
        with pytest.raises(ValueError, match="Remote connection with ID 9999 not found"):
            remote_transfer_service.create_transfer_job(db_session, inv.id, 9999, 1)

    @pytest.mark.asyncio
    async def test_process_pending_transfers_empty(self, db_session, monkeypatch):
        """Test processing transfers when none are pending."""
        # Ensure no pending jobs
        db_session.query(RemoteTransferJob).delete()
        db_session.commit()
        
        # Mocking background logic if needed, but let's see if it just finishes
        await remote_transfer_service.process_pending_transfers()
        # Success if no exception

    def test_get_transfer_timeouts(self):
        """Test timeout configuration helper."""
        from app.services.remote_transfer_service import get_transfer_timeouts
        timeouts = get_transfer_timeouts()
        assert timeouts.connect is not None
        assert timeouts.read is not None
