import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import contextlib

import pytest
from app.models import Criteria, CriterionType, Operator
from app.services.criteria_matcher import CriteriaMatcher


@pytest.fixture
def mock_stat():
    """Fixture to create a mock stat_result object."""

    def _mock_stat(
        st_mtime=None,
        st_atime=None,
        st_ctime=None,
        st_size=0,
        st_mode=0o644,
        st_uid=1000,
        st_gid=1000,
    ):
        stat_result = MagicMock(spec=os.stat_result)
        now = time.time()
        stat_result.st_mtime = st_mtime if st_mtime is not None else now
        stat_result.st_atime = st_atime if st_atime is not None else now
        stat_result.st_ctime = st_ctime if st_ctime is not None else now
        stat_result.st_size = st_size
        stat_result.st_mode = st_mode
        stat_result.st_uid = st_uid
        stat_result.st_gid = st_gid
        return stat_result

    return _mock_stat


@pytest.fixture
def mock_file(tmp_path, mocker):
    """Fixture to create a mock file and optionally mock stat."""

    @contextlib.contextmanager
    def _mock_file(filename="test.txt", *, create_file=True, stat_info=None):
        file_path = tmp_path / filename
        if create_file:
            file_path.touch()

        if stat_info is not None:
            mocker.patch.object(Path, "stat", return_value=stat_info)
            mocker.patch("os.stat", return_value=stat_info)

        yield file_path

    return _mock_file


# ==================================
# Time-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    "operator, file_age_minutes, criterion_value_minutes, expected",
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
def test_match_time_mtime(mock_file, operator, file_age_minutes, criterion_value_minutes, expected):
    """Test MTIME criteria with various operators."""
    now = time.time()
    file_mtime = now - (file_age_minutes * 60)
    stat_info = MagicMock(spec=os.stat_result, st_mtime=file_mtime)

    with mock_file(stat_info=stat_info) as file_path:
        criterion = Criteria(
            criterion_type=CriterionType.MTIME, operator=operator, value=criterion_value_minutes
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


@patch("platform.system", return_value="Linux")
def test_match_time_atime_linux(mock_platform, mock_file):
    """Test ATIME on a non-macOS system."""
    now = time.time()
    file_atime = now - (10 * 60)  # 10 minutes ago
    stat_info = MagicMock(spec=os.stat_result, st_atime=file_atime)

    with mock_file(stat_info=stat_info) as file_path:
        criterion = Criteria(criterion_type=CriterionType.ATIME, operator=Operator.GT, value="5")
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is True

        criterion.value = "15"
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is False


@patch("platform.system", return_value="Darwin")
@patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
def test_match_time_atime_macos_with_last_open(mock_get_last_open, mock_platform, mock_file):
    """Test ATIME on macOS when _get_macos_last_open_time returns a more recent time."""
    now = time.time()
    file_atime = now - (20 * 60)  # atime is 20 minutes ago
    last_open_time = now - (5 * 60)  # last open is 5 minutes ago

    mock_get_last_open.return_value = last_open_time
    stat_info = MagicMock(spec=os.stat_result, st_atime=file_atime)

    with mock_file(stat_info=stat_info) as file_path:
        # Match against last_open_time (5 mins ago), which is > 10 mins ago = False
        criterion = Criteria(criterion_type=CriterionType.ATIME, operator=Operator.GT, value="10")
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is False

        # Match against last_open_time (5 mins ago), which is < 10 mins ago = True
        criterion.operator = Operator.LT
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) is True


@patch("platform.system", return_value="Darwin")
@patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
def test_match_time_atime_macos_with_older_last_open(mock_get_last_open, mock_platform, mock_file):
    """Test ATIME on macOS when atime is more recent than _get_macos_last_open_time."""
    now = time.time()
    file_atime = now - (5 * 60)  # atime is 5 minutes ago
    last_open_time = now - (20 * 60)  # last open is 20 minutes ago

    mock_get_last_open.return_value = last_open_time
    stat_info = MagicMock(spec=os.stat_result, st_atime=file_atime)

    with mock_file(stat_info=stat_info) as file_path:
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
    "operator, file_size, criterion_value, expected",
    [
        # Bytes (c)
        (Operator.GT, 1024, "1000c", True),
        (Operator.LT, 1000, "1k", True),
        (Operator.EQ, 1024, "1k", True),
        # Kilobytes (k)
        (Operator.GT, 2048, "1k", True),
        (Operator.LT, 1000, "1K", True),
        (Operator.EQ, 1024, "1k", True),
        (Operator.GTE, 1024, "1k", True),
        (Operator.LTE, 1024, "1k", True),
        # Megabytes (M)
        (Operator.GT, 2 * 1024 * 1024, "1M", True),
        (Operator.LT, 1024 * 1024 - 1, "1m", True),
        (Operator.EQ, 1024 * 1024, "1M", True),
        # Gigabytes (G)
        (Operator.GT, 2 * 1024 * 1024 * 1024, "1g", True),
        (Operator.EQ, 1024 * 1024 * 1024, "1G", True),
        # Failure cases
        (Operator.GT, 100, "1k", False),
        (Operator.LT, 2048, "1k", False),
        (Operator.EQ, 1023, "1k", False),
    ],
)
def test_match_size(mock_file, operator, file_size, criterion_value, expected):
    """Test SIZE criteria with various operators and suffixes."""
    stat_info = MagicMock(spec=os.stat_result, st_size=file_size)

    with mock_file(stat_info=stat_info) as file_path:
        criterion = Criteria(
            criterion_type=CriterionType.SIZE, operator=operator, value=criterion_value
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


# ==================================
# Name-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    "operator, filename, criterion_value, case_sensitive, expected",
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
def test_match_name(mock_file, operator, filename, criterion_value, case_sensitive, expected):
    """Test NAME and INAME criteria with various operators."""
    stat_info = MagicMock(spec=os.stat_result)

    with mock_file(filename=filename, stat_info=stat_info) as file_path:
        criterion_type = CriterionType.NAME if case_sensitive else CriterionType.INAME
        criterion = Criteria(
            criterion_type=criterion_type, operator=operator, value=criterion_value
        )
        assert CriteriaMatcher._match_criterion(file_path, stat_info, criterion) == expected


# ==================================
# Type-based Criteria Tests
# ==================================


@pytest.mark.parametrize(
    "file_creator, criterion_value, expected",
    [
        (lambda p: p.touch(), "f", True),
        (lambda p: p.mkdir(), "d", True),
        (lambda p: p.symlink_to("target"), "l", True),
        (lambda p: p.touch(), "d", False),
        (lambda p: p.mkdir(), "f", False),
    ],
)
def test_match_type(tmp_path, file_creator, criterion_value, expected, mocker):
    """Test TYPE criteria for file, directory, and symlink."""
    file_path = tmp_path / "test_entity"
    file_creator(file_path)

    criterion = Criteria(
        criterion_type=CriterionType.TYPE, operator=Operator.EQ, value=criterion_value
    )

    # We need to use lstat for symlinks to not follow them
    if file_path.is_symlink():
        stat_info = file_path.lstat()
    else:
        stat_info = file_path.stat()

    # To test file type, we need to mock the is_file, is_dir, is_symlink methods on the Path object
    # But for this test, it's easier to create real files in a temporary directory

    # We create a mock for stat_info because _match_criterion expects it
    mock_stat_info = MagicMock(spec=os.stat_result)

    # Let's mock the Path object's methods for the test
    # Note: Path.is_file/is_dir/is_symlink are methods that call stat internally,
    #       so patching Path.stat might be enough.
    #       However, directly patching these for the test scenario is also valid.
    mocker.patch.object(
        Path, "is_file", return_value=(criterion_value in ["f", "file"] and expected)
    )
    mocker.patch.object(
        Path, "is_dir", return_value=(criterion_value in ["d", "directory"] and expected)
    )
    mocker.patch.object(
        Path, "is_symlink", return_value=(criterion_value in ["l", "link"] and expected)
    )

    assert CriteriaMatcher._match_type(file_path, mock_stat_info, criterion_value) == expected


# ==================================
# Overall Matcher Logic Tests
# ==================================


def test_match_file_all_criteria_match(mock_file):
    """Test that match_file returns True when all criteria match."""
    now = time.time()
    file_mtime = now - (10 * 60)  # 10 mins old
    file_size = 2048  # 2k
    stat_info = MagicMock(spec=os.stat_result, st_mtime=file_mtime, st_size=file_size)

    with mock_file(filename="report-final.pdf", stat_info=stat_info) as file_path:
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


def test_match_file_one_criterion_fails(mock_file):
    """Test that match_file returns False if one criterion does not match."""
    now = time.time()
    file_mtime = now - (10 * 60)  # 10 mins old
    stat_info = MagicMock(spec=os.stat_result, st_mtime=file_mtime, st_size=500)

    with mock_file(filename="report-final.pdf", stat_info=stat_info) as file_path:
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


def test_match_file_no_criteria(mock_file):
    """Test that match_file returns True when no criteria are provided."""
    stat_info = MagicMock(spec=os.stat_result)

    with mock_file(stat_info=stat_info) as file_path:
        matches, matched_ids = CriteriaMatcher.match_file(file_path, [])
        assert matches is True
        assert matched_ids == []


def test_match_file_no_enabled_criteria(mock_file):
    """Test that match_file returns True when all criteria are disabled."""
    stat_info = MagicMock(spec=os.stat_result)

    with mock_file(stat_info=stat_info) as file_path:
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
