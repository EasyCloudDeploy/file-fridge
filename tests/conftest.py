
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import MonitoredPath, ColdStorageLocation, User, OperationType
from app.security import hash_password

# Override settings for testing
settings.database_path = ":memory:"
settings.secret_key = "test-secret-key"  # NOSONAR
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


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset rate limiters before each test to prevent cross-test pollution."""
    from app.utils.rate_limiter import _login_rate_limiter, _remote_rate_limiter

    _login_rate_limiter.requests.clear()
    _remote_rate_limiter.requests.clear()


@pytest.fixture
def authenticated_client(client, db_session):
    """Fixture to get an authenticated client (admin role)."""
    username = "admin"
    password = "password"  # NOSONAR
    user = User(username=username, password_hash=hash_password(password), roles=["admin"])
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},  # NOSONAR
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
def storage_location(db_session):
    """Fixture to create a ColdStorageLocation."""
    location = ColdStorageLocation(
        name="Test Location",
        path="/tmp/cold_storage",
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


@pytest.fixture
def monitored_path_factory(db_session, storage_location):
    """Factory for creating MonitoredPath entries."""
    def _factory(name: str, source_path: str, operation_type: OperationType = OperationType.MOVE):
        path = MonitoredPath(
            name=name,
            source_path=source_path,
            operation_type=operation_type,
            enabled=True
        )
        path.storage_locations.append(storage_location)
        db_session.add(path)
        db_session.commit()
        db_session.refresh(path)
        return path
    return _factory
