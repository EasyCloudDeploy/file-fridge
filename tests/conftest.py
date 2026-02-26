
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.config import settings
from app.utils.rate_limiter import _login_rate_limiter, _remote_rate_limiter

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


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset global rate limiters before each test."""
    _login_rate_limiter.requests.clear()
    _remote_rate_limiter.requests.clear()
    yield
