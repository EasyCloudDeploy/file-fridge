
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import factory
import pytest
from cryptography.fernet import Fernet
from app.models import (
    ColdStorageLocation,
    ConflictResolution,
    Criteria,
    CriterionType,
    DispatchStatus,
    EncryptionManager,
    EncryptionStatus,
    FileInventory,
    FileRecord,
    FileStatus,
    FileTag,
    FileTransferStrategy,
    FileTransactionHistory,
    InstanceKeyHistory,
    InstanceMetadata,
    MonitoredPath,
    Notification,
    NotificationDispatch,
    NotificationLevel,
    Notifier,
    NotifierType,
    OperationType,
    Operator,
    PinnedFile,
    RemoteConnection,
    RemoteTransferJob,
    RequestNonce,
    ScanStatus,
    SecurityAuditLog,
    ServerEncryptionKey,
    StorageType,
    Tag,
    TagRule,
    TagRuleCriterionType,
    TransactionType,
    TransferDirection,
    TransferMode,
    TransferStatus,
    TrustStatus,
    User,
)
from sqlalchemy.orm import Session


# ==================================
# Factory Definitions
# ==================================


class ColdStorageLocationFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ColdStorageLocation
        sqlalchemy_session = None # Will be set by fixture

    id = factory.Sequence(lambda n: n)
    name = factory.Sequence(lambda n: f"Location {n}")
    path = factory.Sequence(lambda n: f"/mnt/cold_storage_{n}")
    is_encrypted = False
    encryption_status = EncryptionStatus.NONE


class MonitoredPathFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = MonitoredPath
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    name = factory.Sequence(lambda n: f"Path {n}")
    source_path = factory.Sequence(lambda n: f"/srv/hot_data_{n}")
    operation_type = OperationType.MOVE
    check_interval_seconds = 3600
    enabled = True
    prevent_indexing = True
    last_scan_status = ScanStatus.SUCCESS

    @factory.post_generation
    def storage_locations(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            for location in extracted:
                self.storage_locations.append(location)
        else:
            # Default to one storage location if none provided
            self.storage_locations.append(ColdStorageLocationFactory(sqlalchemy_session=self.session))


class CriteriaFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Criteria
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    path = factory.SubFactory(MonitoredPathFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    path_id = factory.SelfAttribute("path.id")
    criterion_type = CriterionType.SIZE
    operator = Operator.GT
    value = "1M"
    enabled = True


class FileInventoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = FileInventory
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    path = factory.SubFactory(MonitoredPathFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    path_id = factory.SelfAttribute("path.id")
    file_path = factory.Sequence(lambda n: f"/srv/hot_data_0/file_{n}.txt")
    storage_type = StorageType.HOT
    file_size = 1024
    file_mtime = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    file_atime = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    file_ctime = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    status = FileStatus.ACTIVE


class TagFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Tag
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    name = factory.Sequence(lambda n: f"Tag {n}")
    color = "#FF5733"


class UserFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    username = factory.Sequence(lambda n: f"user{n}")
    password_hash = "hashed_password"
    is_active = True
    roles = ["user"]


class RemoteConnectionFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = RemoteConnection
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    name = factory.Sequence(lambda n: f"Remote {n}")
    url = factory.Sequence(lambda n: f"http://remote_{n}.com")
    remote_fingerprint = factory.Sequence(lambda n: f"fingerprint_{n}")
    remote_ed25519_public_key = "remote_ed25519_pub"
    remote_x25519_public_key = "remote_x25519_pub"
    trust_status = TrustStatus.TRUSTED
    transfer_mode = TransferMode.BIDIRECTIONAL
    remote_transfer_mode = TransferMode.BIDIRECTIONAL


# ==================================
# Fixtures
# ==================================


@pytest.fixture(scope="function", autouse=True)
def set_session_for_factories(db_session: Session):
    """Set the SQLAlchemy session for all factories."""
    ColdStorageLocationFactory._meta.sqlalchemy_session = db_session
    MonitoredPathFactory._meta.sqlalchemy_session = db_session
    CriteriaFactory._meta.sqlalchemy_session = db_session
    FileInventoryFactory._meta.sqlalchemy_session = db_session
    TagFactory._meta.sqlalchemy_session = db_session
    UserFactory._meta.sqlalchemy_session = db_session
    RemoteConnectionFactory._meta.sqlalchemy_session = db_session
    yield
    # Clean up session after tests
    ColdStorageLocationFactory._meta.sqlalchemy_session = None
    MonitoredPathFactory._meta.sqlalchemy_session = None
    CriteriaFactory._meta.sqlalchemy_session = None
    FileInventoryFactory._meta.sqlalchemy_session = None
    TagFactory._meta.sqlalchemy_session = None
    UserFactory._meta.sqlalchemy_session = None
    RemoteConnectionFactory._meta.sqlalchemy_session = None


# ==================================
# Model Tests
# ==================================


def test_cold_storage_location_creation(db_session: Session):
    location = ColdStorageLocationFactory(name="My Cold Storage", path="/data/cold")
    db_session.add(location)
    db_session.commit()
    assert location.id is not None
    assert location.name == "My Cold Storage"
    assert location.path == "/data/cold"
    assert location.encryption_status == EncryptionStatus.NONE


def test_monitored_path_creation(db_session: Session):
    path = MonitoredPathFactory(name="My Path", source_path="/data/hot")
    db_session.add(path)
    db_session.commit()
    assert path.id is not None
    assert path.name == "My Path"
    assert path.source_path == "/data/hot"
    assert path.operation_type == OperationType.MOVE
    assert len(path.storage_locations) == 1 # Default one from post_generation
    assert path.cold_storage_path == path.storage_locations[0].path


def test_criteria_creation(db_session: Session):
    path = MonitoredPathFactory()
    criteria = CriteriaFactory(path=path, criterion_type=CriterionType.MTIME, operator=Operator.LT, value="30")
    db_session.add(criteria)
    db_session.commit()
    assert criteria.id is not None
    assert criteria.path_id == path.id
    assert criteria.criterion_type == CriterionType.MTIME
    assert criteria.operator == Operator.LT
    assert criteria.value == "30"


def test_file_inventory_creation(db_session: Session):
    file = FileInventoryFactory(file_path="/data/hot/my_file.txt", storage_type=StorageType.COLD, file_size=2048)
    db_session.add(file)
    db_session.commit()
    assert file.id is not None
    assert file.file_path == "/data/hot/my_file.txt"
    assert file.storage_type == StorageType.COLD
    assert file.file_size == 2048
    assert file.status == FileStatus.ACTIVE


def test_tag_creation(db_session: Session):
    tag = TagFactory(name="Important", color="#0000FF")
    db_session.add(tag)
    db_session.commit()
    assert tag.id is not None
    assert tag.name == "Important"
    assert tag.color == "#0000FF"


def test_user_creation(db_session: Session):
    user = UserFactory(username="admin_user", roles=["admin"])
    db_session.add(user)
    db_session.commit()
    assert user.id is not None
    assert user.username == "admin_user"
    assert user.roles == ["admin"]


def test_remote_connection_creation(db_session: Session):
    conn = RemoteConnectionFactory(name="My Remote", url="http://myremote.com", trust_status=TrustStatus.PENDING)
    db_session.add(conn)
    db_session.commit()
    assert conn.id is not None
    assert conn.name == "My Remote"
    assert conn.url == "http://myremote.com"
    assert conn.trust_status == TrustStatus.PENDING
    assert conn.effective_bidirectional is True


@patch("app.models.encryption_manager")
def test_notifier_smtp_password_encryption_decryption(mock_encryption_manager, db_session: Session):
    """Test the encryption/decryption of SMTP password on Notifier model."""
    mock_encryption_manager.encrypt.return_value = "encrypted_password"
    mock_encryption_manager.decrypt.return_value = "plaintext_password"

    notifier = Notifier(
        name="Test Email",
        type=NotifierType.EMAIL,
        address="test@example.com",
        smtp_password="plaintext_password",
    )
    db_session.add(notifier)
    db_session.commit()
    db_session.refresh(notifier)

    # Test setter (encryption)
    mock_encryption_manager.encrypt.assert_called_once_with("plaintext_password")
    assert notifier.smtp_password_encrypted == "encrypted_password"

    # Test getter (decryption)
    assert notifier.smtp_password == "plaintext_password"
    mock_encryption_manager.decrypt.assert_called_once_with("encrypted_password")


class FileRecordFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = FileRecord
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    path = factory.SubFactory(MonitoredPathFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    path_id = factory.SelfAttribute("path.id")
    original_path = factory.Sequence(lambda n: f"/src/original_{n}.txt")
    cold_storage_path = factory.Sequence(lambda n: f"/dest/cold_{n}.txt")
    cold_storage_location = factory.SubFactory(ColdStorageLocationFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    cold_storage_location_id = factory.SelfAttribute("cold_storage_location.id")
    file_size = 1000
    operation_type = OperationType.MOVE
    criteria_matched = json.dumps([1, 2])


def test_file_record_creation(db_session: Session):
    file_record = FileRecordFactory()
    db_session.add(file_record)
    db_session.commit()
    assert file_record.id is not None
    assert file_record.file_size == 1000
    assert json.loads(file_record.criteria_matched) == [1, 2]

class FileTransactionHistoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = FileTransactionHistory
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    file = factory.SubFactory(FileInventoryFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    file_id = factory.SelfAttribute("file.id")
    transaction_type = TransactionType.FREEZE
    old_storage_type = StorageType.HOT
    new_storage_type = StorageType.COLD
    success = True
    initiated_by = "test_user"


def test_file_transaction_history_creation(db_session: Session):
    history = FileTransactionHistoryFactory()
    db_session.add(history)
    db_session.commit()
    assert history.id is not None
    assert history.transaction_type == TransactionType.FREEZE
    assert history.file_id == history.file.id

class PinnedFileFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PinnedFile
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    path = factory.SubFactory(MonitoredPathFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    path_id = factory.SelfAttribute("path.id")
    file_path = factory.Sequence(lambda n: f"/pinned/file_{n}.txt")


def test_pinned_file_creation(db_session: Session):
    pinned = PinnedFileFactory()
    db_session.add(pinned)
    db_session.commit()
    assert pinned.id is not None
    assert pinned.file_path.startswith("/pinned/file_")

class FileTagFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = FileTag
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    file = factory.SubFactory(FileInventoryFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    file_id = factory.SelfAttribute("file.id")
    tag = factory.SubFactory(TagFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    tag_id = factory.SelfAttribute("tag.id")
    tagged_by = "admin"

def test_file_tag_creation(db_session: Session):
    file_tag = FileTagFactory()
    db_session.add(file_tag)
    db_session.commit()
    assert file_tag.id is not None
    assert file_tag.file_id == file_tag.file.id
    assert file_tag.tag_id == file_tag.tag.id

class TagRuleFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TagRule
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    tag = factory.SubFactory(TagFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    tag_id = factory.SelfAttribute("tag.id")
    criterion_type = TagRuleCriterionType.EXTENSION
    operator = Operator.EQ
    value = ".mp4"
    enabled = True
    priority = 10


def test_tag_rule_creation(db_session: Session):
    tag_rule = TagRuleFactory()
    db_session.add(tag_rule)
    db_session.commit()
    assert tag_rule.id is not None
    assert tag_rule.tag_id == tag_rule.tag.id
    assert tag_rule.criterion_type == TagRuleCriterionType.EXTENSION


class NotificationFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Notification
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    level = NotificationLevel.INFO
    message = factory.Sequence(lambda n: f"Notification message {n}")


def test_notification_creation(db_session: Session):
    notification = NotificationFactory()
    db_session.add(notification)
    db_session.commit()
    assert notification.id is not None
    assert notification.level == NotificationLevel.INFO

class NotificationDispatchFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = NotificationDispatch
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    notification = factory.SubFactory(NotificationFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    notification_id = factory.SelfAttribute("notification.id")
    notifier = factory.SubFactory(Notifier, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    notifier_id = factory.SelfAttribute("notifier.id")
    status = DispatchStatus.SUCCESS


def test_notification_dispatch_creation(db_session: Session):
    dispatch = NotificationDispatchFactory()
    db_session.add(dispatch)
    db_session.commit()
    assert dispatch.id is not None
    assert dispatch.notification_id == dispatch.notification.id
    assert dispatch.notifier_id == dispatch.notifier.id

class ServerEncryptionKeyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ServerEncryptionKey
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    key_value = factory.Sequence(lambda n: Fernet.generate_key().decode())
    fingerprint = factory.Sequence(lambda n: f"fingerprint_{n}")


def test_server_encryption_key_creation(db_session: Session):
    key = ServerEncryptionKeyFactory()
    db_session.add(key)
    db_session.commit()
    assert key.id is not None
    assert key.key_value is not None
    assert key.fingerprint is not None

class InstanceMetadataFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = InstanceMetadata
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    instance_uuid = factory.Sequence(lambda n: f"uuid_{n}")
    current_key_version = 1


def test_instance_metadata_creation(db_session: Session):
    metadata = InstanceMetadataFactory()
    db_session.add(metadata)
    db_session.commit()
    assert metadata.id is not None
    assert metadata.instance_uuid is not None

class RemoteTransferJobFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = RemoteTransferJob
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    file = factory.SubFactory(FileInventoryFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    file_inventory_id = factory.SelfAttribute("file.id")
    remote_connection = factory.SubFactory(RemoteConnectionFactory, sqlalchemy_session=factory.SelfAttribute("..sqlalchemy_session"))
    remote_connection_id = factory.SelfAttribute("remote_connection.id")
    remote_monitored_path_id = 1
    status = TransferStatus.PENDING
    progress = 0
    current_size = 0
    total_size = 1024
    source_path = factory.SelfAttribute("file.file_path")
    relative_path = factory.Sequence(lambda n: f"relative/path_{n}.txt")
    storage_type = StorageType.HOT
    checksum = "test_checksum"
    direction = TransferDirection.PUSH


def test_remote_transfer_job_creation(db_session: Session):
    job = RemoteTransferJobFactory()
    db_session.add(job)
    db_session.commit()
    assert job.id is not None
    assert job.status == TransferStatus.PENDING
    assert job.file_inventory_id == job.file.id

class RequestNonceFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = RequestNonce
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    fingerprint = factory.Sequence(lambda n: f"fingerprint_{n}")
    nonce = factory.Sequence(lambda n: f"nonce_{n}")
    timestamp = factory.LazyFunction(lambda: int(datetime.now(timezone.utc).timestamp()))


def test_request_nonce_creation(db_session: Session):
    nonce = RequestNonceFactory()
    db_session.add(nonce)
    db_session.commit()
    assert nonce.id is not None
    assert nonce.fingerprint is not None

class SecurityAuditLogFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = SecurityAuditLog
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    event_type = "LOGIN_SUCCESS"
    message = "User logged in successfully"
    initiated_by = "test_user"
    event_metadata = json.dumps({"ip_address": "127.0.0.1"})


def test_security_audit_log_creation(db_session: Session):
    log = SecurityAuditLogFactory()
    db_session.add(log)
    db_session.commit()
    assert log.id is not None
    assert log.event_type == "LOGIN_SUCCESS"

class InstanceKeyHistoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = InstanceKeyHistory
        sqlalchemy_session = None

    id = factory.Sequence(lambda n: n)
    key_version = factory.Sequence(lambda n: n + 1)
    ed25519_public_key = "pub_ed"
    ed25519_private_key_encrypted = "priv_ed_enc"
    x25519_public_key = "pub_x25519"
    x25519_private_key_encrypted = "priv_x25519_enc"
    fingerprint = factory.Sequence(lambda n: f"key_fingerprint_{n}")
    active = True


def test_instance_key_history_creation(db_session: Session):
    key_history = InstanceKeyHistoryFactory()
    db_session.add(key_history)
    db_session.commit()
    assert key_history.id is not None
    assert key_history.key_version == 1
    assert key_history.active is True
