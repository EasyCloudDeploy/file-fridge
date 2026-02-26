
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def patch_workflow_session_factory(db_session):
    """Redirect the internal SessionFactory in file_workflow_service to db_session.

    _process_single_file and _thaw_single_file create their own sessions via
    SessionFactory() (bound to app.database.engine, a separate :memory: DB with no
    tables).  This fixture makes those calls return the test's db_session instead,
    so that all DB operations go through the same in-memory database used by the test.

    close() is suppressed so that internal callers don't destroy the shared session.
    """
    original_close = db_session.close
    db_session.close = lambda: None  # keep session alive across internal close() calls

    with patch(
        "app.services.file_workflow_service.SessionFactory",
        side_effect=lambda: db_session,
    ):
        yield

    db_session.close = original_close  # restore for fixture teardown
