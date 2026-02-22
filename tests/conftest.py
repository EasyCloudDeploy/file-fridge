
import pytest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from pathlib import Path
from datetime import datetime, timezone

from app.database import Base, get_db
from app.main import app
from app.config import settings
from app.models import (
    User,
    ColdStorageLocation,
    MonitoredPath,
    RemoteConnection,
    RemoteTransferJob,
    FileInventory,
    TrustStatus,
    TransferMode,
    TransferDirection,
    StorageType,
    FileStatus,
    FileTransferStrategy,
    ConflictResolution
)
from app.security import hash_password

# Override settings for testing
settings.database_path = ":memory:"
settings.secret_key = "test-secret-key"  # NOSONAR
settings.encryption_key_file = "./test_encryption.key"  # NOSONAR
settings.require_fingerprint_verification = False


# Use an in-memory SQLite database for tests
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Override the get_db dependency to use the test database
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session")
def db_connection():
    # The connection object is created once per session
    connection = engine.connect()
    yield connection
    connection.close()


@pytest.fixture(scope="function")
def db_session(db_connection):
    # Before each test, create all tables
    Base.metadata.create_all(bind=engine)
    # A transaction is started
    transaction = db_connection.begin()
    # A session is created, bound to the connection
    session = TestingSessionLocal(bind=db_connection)
    yield session
    # The session is closed
    session.close()
    # The transaction is rolled back
    transaction.rollback()
    # After each test, drop all tables
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """
    Create a new FastAPI TestClient that uses the `db_session` fixture to override
    the `get_db` dependency that is injected into routes.
    """

    def _override_get_db():
        try:
            yield db_session
        finally:
            # Do NOT close the session here, as it is shared with the test function.
            # The db_session fixture will close it after the test.
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="function")
def authenticated_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client."""
    username = "authtestuser"
    password = "password"
    # Check if user already exists (e.g. from previous test run if cleanup failed)
    existing_user = db_session.query(User).filter(User.username == username).first()
    if not existing_user:
        user = User(username=username, password_hash=hash_password(password), roles=["admin"])
        db_session.add(user)
        db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code != 200:
        # Fallback if login fails (e.g. rate limit)
        # Create a token manually
        from app.security import create_access_token

        token = create_access_token({"sub": username, "roles": ["admin"]})
    else:
        token = response.json()["access_token"]

    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
def storage_location(db_session: Session):
    """Fixture for a ColdStorageLocation object."""
    # Ensure directory exists in /tmp
    path = Path("/tmp/cold_storage")
    path.mkdir(exist_ok=True, parents=True)

    location = ColdStorageLocation(
        name="Test Cold Storage",
        path=str(path),
        # is_default=True, # Removed as it does not exist in the model
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


@pytest.fixture
def monitored_path_factory(db_session: Session, storage_location: ColdStorageLocation):
    """Factory fixture to create MonitoredPath objects."""

    def _factory(name: str, source_path: str):
        path = MonitoredPath(
            name=name,
            source_path=source_path,
            storage_locations=[storage_location],
        )
        db_session.add(path)
        db_session.commit()
        db_session.refresh(path)
        # Create the directory
        # Handle permission error by ignoring if it fails (e.g. /root) or verify path is writable
        try:
            Path(path.source_path).mkdir(exist_ok=True, parents=True)
        except PermissionError:
            # If we can't create it, we assume the test will handle it or use a different path
            pass
        return path

    return _factory

@pytest.fixture
def file_inventory_factory(db_session: Session, monitored_path_factory):
    """Factory for FileInventory objects."""
    def _factory(path_id: int = None, file_path: str = None):
        if path_id is None:
            # Create a path if needed
            unique_id = str(uuid.uuid4())[:8]
            path = monitored_path_factory(f"File Path {unique_id}", f"/tmp/files_{unique_id}")
            path_id = path.id

        if file_path is None:
            file_path = f"/tmp/file_{uuid.uuid4()}.txt"

        file_obj = FileInventory(
            path_id=path_id,
            file_path=file_path,
            storage_type=StorageType.HOT,
            file_size=1024,
            file_mtime=datetime.now(timezone.utc),
            status=FileStatus.ACTIVE,
            checksum="abc123checksum",
            file_extension=".txt"
        )
        db_session.add(file_obj)
        db_session.commit()
        db_session.refresh(file_obj)
        return file_obj
    return _factory

@pytest.fixture
def remote_connection_factory(db_session: Session):
    """Factory for RemoteConnection objects."""

    def _factory(
        name: str = "Test Remote",
        url: str = "http://remote.example.com",
        fingerprint: str = "testfingerprint",
        trust_status: TrustStatus = TrustStatus.TRUSTED,
        remote_transfer_mode: TransferMode = TransferMode.BIDIRECTIONAL,
        effective_bidirectional: bool = True,
    ):
        # Generate unique values if defaults are used to avoid unique constraint violations
        if name == "Test Remote":
            unique_id = str(uuid.uuid4())[:8]
            name = f"Test Remote {unique_id}"

        if fingerprint == "testfingerprint":
            unique_id = str(uuid.uuid4())[:8]
            fingerprint = f"fp_{unique_id}"

        conn = RemoteConnection(
            name=name,
            url=url,
            remote_fingerprint=fingerprint,
            remote_ed25519_public_key="pubkey",
            remote_x25519_public_key="xpubkey",
            trust_status=trust_status,
            transfer_mode=remote_transfer_mode if effective_bidirectional else TransferMode.PUSH_ONLY,
            remote_transfer_mode=remote_transfer_mode if effective_bidirectional else TransferMode.PUSH_ONLY,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(conn)
        db_session.commit()
        db_session.refresh(conn)
        return conn

    return _factory


@pytest.fixture
def remote_transfer_job_factory(
    db_session: Session, remote_connection_factory, monitored_path_factory, file_inventory_factory
):
    """Factory for RemoteTransferJob objects."""

    def _factory(
        file_inventory_id: int = None,
        remote_connection: RemoteConnection = None,
        remote_monitored_path: MonitoredPath = None,
        direction: TransferDirection = TransferDirection.PUSH,
        status: str = "PENDING",
        file_name: str = "test_file.txt",
        file_size: int = 1024,
    ):
        if remote_connection is None:
            remote_connection = remote_connection_factory()
        if remote_monitored_path is None:
            # Use a safe path in /tmp and unique name
            unique_id = str(uuid.uuid4())[:8]
            safe_path = f"/tmp/remote_path_{unique_id}"
            remote_monitored_path = monitored_path_factory(f"Remote Path {unique_id}", safe_path)

        if file_inventory_id is None:
            # Create a file inventory record to satisfy FK
            file_inv = file_inventory_factory()
            file_inventory_id = file_inv.id

        job = RemoteTransferJob(
            file_inventory_id=file_inventory_id,
            remote_connection_id=remote_connection.id,
            remote_monitored_path_id=remote_monitored_path.id,
            direction=direction,
            status=status,
            total_size=file_size,
            source_path=f"/tmp/{file_name}",
            relative_path=file_name,
            storage_type="hot",
            checksum="testchecksum",
            start_time=datetime.now(timezone.utc),
            strategy=FileTransferStrategy.COPY,
            conflict_resolution=ConflictResolution.OVERWRITE
            # created_at removed
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job

    return _factory
