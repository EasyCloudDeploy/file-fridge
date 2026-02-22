import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from pathlib import Path

from app.database import Base, get_db
from app.main import app
from app.config import settings
from app.models import User, ColdStorageLocation, MonitoredPath
from app.security import hash_password

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
    location = ColdStorageLocation(
        name="Test Cold Storage",
        path="/tmp/cold_storage",
        # is_default=True, # Removed as it does not exist in the model
    )
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    # Create the directory
    Path(location.path).mkdir(exist_ok=True, parents=True)
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
        Path(path.source_path).mkdir(exist_ok=True, parents=True)
        return path

    return _factory
