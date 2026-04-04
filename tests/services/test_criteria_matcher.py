import contextlib
import os
import time
from unittest.mock import patch

import pytest

from app.models import Criteria, CriterionType, Operator
from app.services.criteria_matcher import CriteriaMatcher


@pytest.fixture
def real_file(tmp_path):
    """Fixture to create a real file with specific attributes."""

    @contextlib.contextmanager
    def _real_file(filename="test.txt", size=0, mtime=None, atime=None, content=None):
        file_path = tmp_path / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if content:
            file_path.write_text(content)
        else:
            file_path.touch()

        if size > 0:
            with file_path.open("wb") as f:
                f.truncate(size)

        if mtime is not None or atime is not None:
            # If one is None, use current time
            now = time.time()
            m_t = mtime if mtime is not None else now
            a_t = atime if atime is not None else now
            os.utime(file_path, (a_t, m_t))

        yield file_path

    return _real_file


# ==================================
# Time-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    ("operator", "file_age_minutes", "criterion_value_minutes", "expected"),
    [
        (Operator.GT, 60, "30", True),  # Older than 30 mins -> True
        (Operator.GT, 20, "30", False),  # Newer than 30 mins -> False
        (Operator.LT, 20, "30", True),  # Newer than 30 mins -> True
        (Operator.LT, 60, "30", False),  # Older than 30 mins -> False
        (Operator.EQ, 30, "30", True),  # Exactly 30 mins -> True
        (Operator.EQ, 29.6, "30", True),  # Within tolerance -> True
        (Operator.EQ, 30.4, "30", True),  # Within tolerance -> True
        (Operator.EQ, 31, "30", False),  # Outside tolerance -> False
    ],
)
def test_match_time_mtime(real_file, operator, file_age_minutes, criterion_value_minutes, expected):
    """Test MTIME criteria with various operators using real files."""
    now = time.time()
    file_mtime = now - (file_age_minutes * 60)

    with real_file(mtime=file_mtime) as file_path:
        stat_info = file_path.stat()
        criterion = Criteria(
            criterion_type=CriterionType.MTIME, operator=operator, value=criterion_value_minutes
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


@patch("platform.system", return_value="Linux")
def test_match_time_atime_linux(mock_platform, real_file):
    """Test ATIME on a non-macOS system using real files."""
    now = time.time()
    file_atime = now - (10 * 60)  # 10 minutes ago

    with real_file(atime=file_atime) as file_path:
        stat_info = file_path.stat()
        criterion = Criteria(criterion_type=CriterionType.ATIME, operator=Operator.GT, value="5")
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is True

        criterion.value = "15"
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is False


@patch("platform.system", return_value="Darwin")
@patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
def test_match_time_atime_macos_with_last_open(mock_get_last_open, mock_platform, real_file):
    """Test ATIME on macOS when _get_macos_last_open_time returns a more recent time."""
    now = time.time()
    file_atime = now - (20 * 60)  # atime is 20 minutes ago
    last_open_time = now - (5 * 60)  # last open is 5 minutes ago

    mock_get_last_open.return_value = last_open_time

    with real_file(atime=file_atime) as file_path:
        stat_info = file_path.stat()
        # Match against last_open_time (5 mins ago), which is > 10 mins ago = False (age < 10)
        # Wait, if age is 5 mins, and criterion is > 10 mins, it should be False.
        criterion = Criteria(criterion_type=CriterionType.ATIME, operator=Operator.GT, value="10")
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is False

        # Match against last_open_time (5 mins ago), which is < 10 mins ago = True
        criterion.operator = Operator.LT
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is True


@patch("platform.system", return_value="Darwin")
@patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
def test_match_time_atime_macos_with_older_last_open(mock_get_last_open, mock_platform, real_file):
    """Test ATIME on macOS when atime is more recent than _get_macos_last_open_time."""
    now = time.time()
    file_atime = now - (5 * 60)  # atime is 5 minutes ago
    last_open_time = now - (20 * 60)  # last open is 20 minutes ago

    mock_get_last_open.return_value = last_open_time

    with real_file(atime=file_atime) as file_path:
        stat_info = file_path.stat()
        # Match against atime (5 mins ago), which is > 10 mins ago = False
        criterion = Criteria(criterion_type=CriterionType.ATIME, operator=Operator.GT, value="10")
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is False

        # Match against atime (5 mins ago), which is < 10 mins ago = True
        criterion.operator = Operator.LT
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is True


# ==================================
# Size-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    ("operator", "file_size", "criterion_value", "expected"),
    [
        (Operator.GT, 1024, "1000c", True),
        (Operator.LT, 1000, "1k", True),
        (Operator.EQ, 1024, "1k", True),
        (Operator.GT, 2048, "1k", True),
        (Operator.LT, 1000, "1K", True),
        (Operator.GTE, 1024, "1k", True),
        (Operator.LTE, 1024, "1k", True),
        (Operator.GT, 2 * 1024 * 1024, "1M", True),
        (Operator.LT, 1024 * 1024 - 1, "1m", True),
        (Operator.EQ, 1024 * 1024, "1M", True),
        (Operator.GT, 2 * 1024 * 1024 * 1024, "1g", True),
        (Operator.EQ, 1024 * 1024 * 1024, "1G", True),
        (Operator.GT, 100, "1k", False),
        (Operator.LT, 2048, "1k", False),
        (Operator.EQ, 1023, "1k", False),
    ],
)
def test_match_size(real_file, operator, file_size, criterion_value, expected):
    """Test SIZE criteria with various operators and suffixes using real files."""
    # Note: Creating a 2GB file for testing might be slow/heavy,
    # but truncate() is usually very fast on modern filesystems (sparse files).
    with real_file(size=file_size) as file_path:
        stat_info = file_path.stat()
        criterion = Criteria(
            criterion_type=CriterionType.SIZE, operator=operator, value=criterion_value
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


# ==================================
# Name-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    ("operator", "filename", "criterion_value", "case_sensitive", "expected"),
    [
        (Operator.EQ, "test.txt", "test.txt", True, True),
        (Operator.EQ, "test.txt", "Test.txt", True, False),
        (Operator.EQ, "test.txt", "Test.txt", False, True),
        (Operator.CONTAINS, "this is a test.log", "is a test", True, True),
        (Operator.CONTAINS, "this is a test.log", "IS A TEST", True, False),
        (Operator.CONTAINS, "this is a test.log", "IS A TEST", False, True),
        (Operator.MATCHES, "file-01.jpg", "file-*.jpg", True, True),
        (Operator.MATCHES, "file-01.JPG", "file-*.jpg", True, False),
        (Operator.MATCHES, "file-01.JPG", "file-*.jpg", False, True),
        (Operator.REGEX, "document_2024_final.pdf", r"\d{4}", True, True),
        (Operator.REGEX, "photo.JPEG", r"\.(jpeg|jpg)$", True, False),
        (Operator.REGEX, "photo.JPEG", r"\.(jpeg|jpg)$", False, True),
    ],
)
def test_match_name(real_file, operator, filename, criterion_value, case_sensitive, expected):
    """Test NAME and INAME criteria with various operators using real files."""
    with real_file(filename=filename) as file_path:
        stat_info = file_path.stat()
        criterion_type = CriterionType.NAME if case_sensitive else CriterionType.INAME
        criterion = Criteria(
            criterion_type=criterion_type, operator=operator, value=criterion_value
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


# ==================================
# Type-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    ("file_creator", "criterion_value", "expected"),
    [
        (lambda p: p.touch(), "f", True),
        (lambda p: p.mkdir(), "d", True),
        (lambda p: p.symlink_to("target"), "l", True),
        (lambda p: p.touch(), "d", False),
        (lambda p: p.mkdir(), "f", False),
    ],
)
def test_match_type(tmp_path, file_creator, criterion_value, expected):
    """Test TYPE criteria for file, directory, and symlink using real entities."""
    file_path = tmp_path / f"test_entity_{criterion_value}_{expected}"
    if file_path.exists():
         import shutil
         if file_path.is_dir() and not file_path.is_symlink():
             shutil.rmtree(file_path)
         else:
             file_path.unlink()

    file_creator(file_path)

    # We need to use lstat for symlinks to not follow them
    stat_info = file_path.lstat()

    assert CriteriaMatcher._match_type(file_path, stat_info, criterion_value) == expected


# ==================================
# Overall Matcher Logic Tests
# ==================================


def test_match_file_all_criteria_match(real_file):
    """Test that match_file returns True when all criteria match with real files."""
    now = time.time()
    file_mtime = now - (10 * 60)  # 10 mins old
    file_size = 2048  # 2k

    with real_file(filename="report-final.pdf", size=file_size, mtime=file_mtime) as file_path:
        criteria = [
            Criteria(
                id=1,
                enabled=True,
                criterion_type=CriterionType.MTIME,
                operator=Operator.GT,
                value="5",
            ),
            Criteria(
                id=2,
                enabled=True,
                criterion_type=CriterionType.SIZE,
                operator=Operator.GT,
                value="1k",
            ),
            Criteria(
                id=3,
                enabled=True,
                criterion_type=CriterionType.NAME,
                operator=Operator.CONTAINS,
                value="report",
            ),
        ]

        matches, matched_ids = CriteriaMatcher.match_file(file_path, criteria)

        assert matches is True
        assert sorted(matched_ids) == [1, 2, 3]


def test_match_file_one_criterion_fails(real_file):
    """Test that match_file returns False if one criterion does not match."""
    now = time.time()
    file_mtime = now - (10 * 60)  # 10 mins old
    file_size = 500

    with real_file(filename="report-final.pdf", size=file_size, mtime=file_mtime) as file_path:
        criteria = [
            Criteria(
                id=1,
                enabled=True,
                criterion_type=CriterionType.MTIME,
                operator=Operator.GT,
                value="5",
            ),
            Criteria(
                id=2,
                enabled=True,
                criterion_type=CriterionType.SIZE,
                operator=Operator.GT,
                value="1k",
            ),  # This will fail
            Criteria(
                id=3,
                enabled=True,
                criterion_type=CriterionType.NAME,
                operator=Operator.CONTAINS,
                value="report",
            ),
        ]

        matches, matched_ids = CriteriaMatcher.match_file(file_path, criteria)

        assert matches is False
        assert matched_ids == []


def test_match_file_no_criteria(real_file):
    """Test that match_file returns True when no criteria are provided."""
    with real_file() as file_path:
        matches, matched_ids = CriteriaMatcher.match_file(file_path, [])
        assert matches is True
        assert matched_ids == []


def test_match_file_no_enabled_criteria(real_file):
    """Test that match_file returns True when all criteria are disabled."""
    with real_file() as file_path:
        criteria = [
            Criteria(
                id=1,
                enabled=False,
                criterion_type=CriterionType.MTIME,
                operator=Operator.LT,
                value="1",
            ),
        ]
        matches, matched_ids = CriteriaMatcher.match_file(file_path, criteria)
        assert matches is True
        assert matched_ids == []


def test_match_file_not_found(tmp_path):
    """Test that match_file returns False for a non-existent file."""
    file_path = tmp_path / "non_existent_file.txt"
    criterion = [
        Criteria(
            criterion_type=CriterionType.NAME, operator=Operator.EQ, value="test", enabled=True
        )
    ]

    matches, matched_ids = CriteriaMatcher.match_file(file_path, criterion)

    assert matches is False
    assert matched_ids == []
