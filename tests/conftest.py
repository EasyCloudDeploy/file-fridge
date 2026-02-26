import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.config import settings
from app.models import User, MonitoredPath, ColdStorageLocation, FileInventory, FileStatus, StorageType, Tag
from app.security import hash_password
from app.utils.rate_limiter import _login_rate_limiter, _remote_rate_limiter

# Set TESTING environment variable for rate limiter bypass
os.environ["TESTING"] = "true"

# Override settings for testing
settings.database_path = ":memory:"
settings.secret_key = "test-secret-key"
settings.encryption_key_file = "./test_encryption.key"
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


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset rate limiter state between tests to prevent cross-test pollution."""
    _login_rate_limiter.requests.clear()
    _remote_rate_limiter.requests.clear()
    yield
    _login_rate_limiter.requests.clear()
    _remote_rate_limiter.requests.clear()


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
            db_session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="function")
def authenticated_client(client: TestClient, db_session: Session):
    """Fixture to get an authenticated client."""
    username = "authtestuser"
    password = "password"
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture(scope="function")
def storage_location(db_session: Session):
    """Fixture for a ColdStorageLocation object."""
    location = ColdStorageLocation(
        name="Test Cold Storage",
        path="/tmp/cold_storage",
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    # Create the directory
    Path(location.path).mkdir(exist_ok=True, parents=True)
    return location


@pytest.fixture(scope="function")
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
        Path(path.source_path).mkdir(exist_ok=True, parents=True)
        return path
    return _factory


@pytest.fixture(scope="function")
def file_inventory_factory(db_session: Session, monitored_path_factory):
    """Fixture for creating FileInventory objects."""
    def _create_file(
        path="/tmp/test.txt",
        size=1024,
        status=FileStatus.ACTIVE,
        storage_type=StorageType.HOT,
        **kwargs,
    ):
        path_name = kwargs.pop("path_name", "test_path")
        is_pinned = kwargs.pop("is_pinned", False)
        # Handle cold_storage_location object → id conversion
        cold_storage_location = kwargs.pop("cold_storage_location", None)
        if cold_storage_location is not None and "cold_storage_location_id" not in kwargs:
            kwargs["cold_storage_location_id"] = cold_storage_location.id
        # Ensure parent directory exists for monitored_path_factory
        Path(path).parent.mkdir(exist_ok=True, parents=True)
        monitored_path = monitored_path_factory(path_name, str(Path(path).parent))

        now = datetime.now(timezone.utc)
        file_inv = FileInventory(
            path_id=monitored_path.id,
            file_path=path,
            file_size=size,
            file_mtime=kwargs.pop("file_mtime", now),
            status=status,
            storage_type=storage_type,
            **kwargs,
        )
        db_session.add(file_inv)
        db_session.commit()
        db_session.refresh(file_inv)

        if is_pinned:
            from app.models import PinnedFile
            pin = PinnedFile(path_id=file_inv.path_id, file_path=file_inv.file_path)
            db_session.add(pin)
            db_session.commit()

        return file_inv

    return _create_file


@pytest.fixture(scope="function")
def create_tag(db_session: Session):
    """Fixture for creating tags."""
    def _create_tag(name: str, color: str = "#000000"):
        tag = Tag(name=name, color=color)
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)
        return tag

    return _create_tag


@pytest.fixture(scope="function")
def remote_connection_factory(db_session: Session):
    """Factory for RemoteConnection objects."""
    def _factory(
        name: str = "Test Remote",
        url: str = "http://remote.example.com",
        fingerprint: str = "testfingerprint",
        trust_status = "TRUSTED",
        remote_transfer_mode = "BIDIRECTIONAL",
    ):
        from app.models import RemoteConnection, TrustStatus, TransferMode
        conn = RemoteConnection(
            name=name,
            url=url,
            remote_fingerprint=fingerprint,
            remote_ed25519_public_key="3YGphBPL4ioYr/v66Frj0IxKQZrBuSBbO2VXLWp1L5Q=",
            remote_x25519_public_key="rhjY0TbxIvRP94vmjq8LLBCEbjefwurwSe35qQIo1EA=",
            trust_status=trust_status,
            remote_transfer_mode=remote_transfer_mode,
            transfer_mode=TransferMode.BIDIRECTIONAL
        )
        db_session.add(conn)
        db_session.commit()
        db_session.refresh(conn)
        return conn
    return _factory


@pytest.fixture(scope="function")
def remote_transfer_job_factory(db_session: Session, remote_connection_factory, monitored_path_factory, tmp_path):
    """Factory for RemoteTransferJob objects."""
    import itertools
    _counter = itertools.count(1)

    def _factory(
        file_inventory_id: int = 1,
        remote_connection = None,
        remote_monitored_path = None,
        direction = "PUSH",
        status = "PENDING",
    ):
        from app.models import RemoteTransferJob, TransferDirection, TransferStatus, StorageType
        n = next(_counter)
        if remote_connection is None:
            remote_connection = remote_connection_factory(fingerprint=f"testfingerprint{n}")
        if remote_monitored_path is None:
            remote_monitored_path = monitored_path_factory(f"Remote Path {n}", str(tmp_path / f"remote{n}"))

        if isinstance(direction, str):
            direction = TransferDirection[direction]
        if isinstance(status, str):
            status = TransferStatus[status]

        job = RemoteTransferJob(
            file_inventory_id=file_inventory_id,
            remote_connection_id=remote_connection.id,
            remote_monitored_path_id=remote_monitored_path.id,
            direction=direction,
            status=status,
            source_path=str(tmp_path),
            relative_path=f"test_file_{n}.txt",
            total_size=1024,
            storage_type=StorageType.HOT,
            checksum="testchecksum",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job
    return _factory
