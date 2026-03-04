from datetime import datetime, timezone

import pytest

from app.models import FileInventory, StorageType
from app.utils.db_utils import escape_like_string


@pytest.fixture
def seeded_session(db_session):
    now = datetime.now(timezone.utc)
    files = [
        FileInventory(
            file_path="/data/project/file1.txt",
            storage_type=StorageType.HOT,
            path_id=1,
            file_size=100,
            file_mtime=now,
        ),
        FileInventory(
            file_path="/data/project_backup/file2.txt",
            storage_type=StorageType.HOT,
            path_id=1,
            file_size=100,
            file_mtime=now,
        ),
        FileInventory(
            file_path="/data/project%/file3.txt",
            storage_type=StorageType.HOT,
            path_id=1,
            file_size=100,
            file_mtime=now,
        ),
        FileInventory(
            file_path="/data/project_matched/file4.txt",
            storage_type=StorageType.HOT,
            path_id=1,
            file_size=100,
            file_mtime=now,
        ),
    ]
    db_session.add_all(files)
    db_session.commit()
    return db_session


def test_escape_like_string():
    assert escape_like_string("test") == "test"
    assert escape_like_string("test%") == "test\\%"
    assert escape_like_string("test_") == "test\\_"
    assert escape_like_string("test\\") == "test\\\\"
    assert escape_like_string("test%_\\") == "test\\%\\_\\\\"


def test_browser_wildcard_injection(seeded_session):
    # Simulate browsing "/data/project%"
    # Logic from app/routers/api/browser.py
    resolved_path = "/data/project%"
    escaped_path = escape_like_string(resolved_path)

    results = (
        seeded_session.query(FileInventory.file_path)
        .filter(FileInventory.file_path.like(f"{escaped_path}/%", escape="\\"))
        .all()
    )

    paths = [r[0] for r in results]

    # Should match ONLY files inside "/data/project%/"
    assert "/data/project%/file3.txt" in paths
    assert "/data/project/file1.txt" not in paths
    assert "/data/project_backup/file2.txt" not in paths
    assert "/data/project_matched/file4.txt" not in paths
    assert len(paths) == 1


def test_files_wildcard_injection_ilike(seeded_session):
    # Simulate searching for "project%"
    # Logic from app/routers/api/files.py and app/routers/api/remote.py
    search_term = "project%"
    escaped_search = escape_like_string(search_term)
    search_pattern = f"%{escaped_search}%"

    results = (
        seeded_session.query(FileInventory.file_path)
        .filter(FileInventory.file_path.ilike(search_pattern, escape="\\"))
        .all()
    )

    paths = [r[0] for r in results]

    # Should match ONLY files containing literal "project%"
    assert "/data/project%/file3.txt" in paths
    assert "/data/project/file1.txt" not in paths
    assert "/data/project_backup/file2.txt" not in paths
    assert "/data/project_matched/file4.txt" not in paths
    assert len(paths) == 1


def test_storage_wildcard_injection(seeded_session):
    # Simulate deleting storage location "/data/project"
    # Logic from app/routers/api/storage.py
    location_path = "/data/project"

    # Ensure path ends with slash
    prefix = location_path if location_path.endswith("/") else f"{location_path}/"
    escaped_path = escape_like_string(prefix)

    results = (
        seeded_session.query(FileInventory.file_path)
        .filter(FileInventory.file_path.like(f"{escaped_path}%", escape="\\"))
        .all()
    )

    paths = [r[0] for r in results]

    # Should match ONLY files inside "/data/project/"
    assert "/data/project/file1.txt" in paths
    assert "/data/project_backup/file2.txt" not in paths
    assert "/data/project%/file3.txt" not in paths
    assert "/data/project_matched/file4.txt" not in paths
    assert len(paths) == 1
