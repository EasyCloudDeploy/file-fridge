"""Comprehensive tests for Path Criteria matching.

Tests cover all CriterionTypes and Operators with many different file types,
using real hot/cold storage directories set up per-test and cleaned up automatically
via pytest's tmp_path fixture.

Criteria semantics (IMPORTANT):
    Criteria define what files to KEEP in hot storage.
    - match_file returns (True, ids) → file STAYS in hot storage
    - match_file returns (False, []) → file should be MOVED to cold storage

Example:
    mtime < 60  → "keep files modified less than 60 minutes ago"
    If a file is 2 hours old, age_minutes=120, 120 < 60 is False → move to cold.
"""

import grp
import os
import platform
import pwd
import stat
import struct
import time
import zipfile
import zlib
from io import BytesIO
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from app.models import (
    ColdStorageLocation,
    Criteria,
    CriterionType,
    FileInventory,
    FileStatus,
    MonitoredPath,
    OperationType,
    Operator,
    ScanStatus,
    StorageType,
)
from app.services.criteria_matcher import CriteriaMatcher
from app.services.file_workflow_service import FileWorkflowService


# ==============================================================================
# File Factory Helpers – creates many different real file types
# ==============================================================================


def make_text_file(path: Path, content: str = "Hello, World!\n") -> Path:
    """Plain UTF-8 text file."""
    path.write_text(content, encoding="utf-8")
    return path


def make_log_file(path: Path) -> Path:
    """Realistic log file with multiple lines."""
    lines = [
        "2024-01-15 10:30:00 INFO  Server starting up",
        "2024-01-15 10:30:01 DEBUG Loading configuration from /etc/app/config.yaml",
        "2024-01-15 10:30:02 INFO  Listening on 0.0.0.0:8080",
        "2024-01-15 10:31:00 WARN  High memory usage: 82%",
        "2024-01-15 10:32:00 ERROR Connection refused: retrying in 5 s",
        "2024-01-15 10:32:05 INFO  Reconnected successfully",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_json_file(path: Path) -> Path:
    """JSON configuration file."""
    import json

    data = {
        "version": "2.1.0",
        "settings": {"debug": False, "port": 8080, "workers": 4},
        "database": {"engine": "postgresql", "host": "localhost", "port": 5432},
        "tags": ["production", "backend", "api"],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def make_csv_file(path: Path) -> Path:
    """CSV data file."""
    rows = [
        "id,name,score,category",
        "1,Alice,95.5,A",
        "2,Bob,87.2,B",
        "3,Charlie,72.1,C",
        "4,Diana,99.0,A",
        "5,Eve,60.8,D",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def make_xml_file(path: Path) -> Path:
    """XML document."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<root>\n"
        '  <record id="1"><name>First</name><value>100</value></record>\n'
        '  <record id="2"><name>Second</name><value>200</value></record>\n'
        "</root>\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_config_file(path: Path) -> Path:
    """INI-style configuration file."""
    content = (
        "[server]\n"
        "host = 0.0.0.0\n"
        "port = 8080\n"
        "workers = 4\n\n"
        "[database]\n"
        "engine = sqlite\n"
        "path = /var/lib/app/data.db\n\n"
        "[logging]\n"
        "level = INFO\n"
        "file = /var/log/app.log\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_markdown_file(path: Path) -> Path:
    """Markdown document."""
    content = (
        "# Project Documentation\n\n"
        "## Overview\n"
        "This project manages cold-storage tiering for files.\n\n"
        "## Installation\n"
        "```bash\nuv run uvicorn app.main:app --reload\n```\n\n"
        "## License\nApache-2.0\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_python_file(path: Path) -> Path:
    """Python source file."""
    content = (
        '"""Example module."""\n\n'
        "def greet(name: str) -> str:\n"
        '    """Return greeting."""\n'
        '    return f"Hello, {name}!"\n\n\n'
        'if __name__ == "__main__":\n'
        '    print(greet("World"))\n'
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_shell_script(path: Path) -> Path:
    """Shell script with executable permission."""
    content = "#!/bin/bash\nset -e\necho 'Starting setup...'\nmkdir -p /var/app\necho 'Done.'\n"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def make_binary_file(path: Path, size: int = 1024) -> Path:
    """Raw binary file of given byte size."""
    # Deterministic but varied byte pattern
    data = bytes((i * 37 + 13) % 256 for i in range(size))
    path.write_bytes(data)
    return path


def make_png_file(path: Path) -> Path:
    """Minimal valid 1×1 RGB PNG image."""
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\xFF\x00\x00"  # no-filter + RGB pixel
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")

    path.write_bytes(sig + ihdr + idat + iend)
    return path


def make_jpeg_file(path: Path) -> Path:
    """Minimal valid JPEG file (SOI + APP0 + EOI)."""
    data = (
        b"\xff\xd8"  # SOI
        b"\xff\xe0"  # APP0 marker
        b"\x00\x10"  # length 16
        b"JFIF\x00"  # identifier
        b"\x01\x01"  # version
        b"\x00"  # units
        b"\x00\x01\x00\x01"  # density
        b"\x00\x00"  # thumbnail size
        b"\xff\xd9"  # EOI
    )
    path.write_bytes(data)
    return path


def make_pdf_file(path: Path) -> Path:
    """Minimal valid PDF file."""
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj\n"
        b"3 0 obj<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"trailer<</Size 4 /Root 1 0 R>>\nstartxref\n%%EOF\n"
    )
    path.write_bytes(content)
    return path


def make_zip_file(path: Path) -> Path:
    """Valid ZIP archive with two entries."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "This is a test archive.\n")
        zf.writestr("data.json", '{"status": "ok"}\n')
    path.write_bytes(buf.getvalue())
    return path


def make_empty_file(path: Path) -> Path:
    """Zero-byte file."""
    path.touch()
    return path


def make_large_file(path: Path, size_mb: int = 2) -> Path:
    """File of specified size in MiB."""
    chunk = b"A" * (1024 * 1024)
    with open(str(path), "wb") as f:
        for _ in range(size_mb):
            f.write(chunk)
    return path


def make_small_file(path: Path, size_bytes: int = 512) -> Path:
    """Very small file of specified byte count."""
    path.write_bytes(b"x" * size_bytes)
    return path


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def hot_storage(tmp_path) -> Path:
    """Dedicated hot storage directory, auto-cleaned by tmp_path."""
    d = tmp_path / "hot"
    d.mkdir()
    return d


@pytest.fixture
def cold_storage(tmp_path) -> Path:
    """Dedicated cold storage directory, auto-cleaned by tmp_path."""
    d = tmp_path / "cold"
    d.mkdir()
    return d


@pytest.fixture
def make_criterion():
    """Factory for Criteria objects with auto-incrementing IDs."""
    counter = [1]

    def _factory(
        criterion_type: CriterionType,
        operator: Operator,
        value: str,
        *,
        enabled: bool = True,
    ) -> Criteria:
        c = Criteria(
            id=counter[0],
            criterion_type=criterion_type,
            operator=operator,
            value=value,
            enabled=enabled,
        )
        counter[0] += 1
        return c

    return _factory


@pytest.fixture
def file_suite(hot_storage) -> dict:
    """
    Create a comprehensive set of test files in hot storage.

    Returns a dict mapping short labels to their Path objects.
    Sub-directory files are included to test path traversal.
    """
    files: dict = {}

    # ── Text-based formats ──────────────────────────────────────────────────
    files["txt"] = make_text_file(hot_storage / "document.txt", "Simple text document\n")
    files["log"] = make_log_file(hot_storage / "application.log")
    files["json"] = make_json_file(hot_storage / "config.json")
    files["csv"] = make_csv_file(hot_storage / "data.csv")
    files["xml"] = make_xml_file(hot_storage / "settings.xml")
    files["cfg"] = make_config_file(hot_storage / "app.cfg")
    files["md"] = make_markdown_file(hot_storage / "README.md")
    files["py"] = make_python_file(hot_storage / "module.py")
    files["sh"] = make_shell_script(hot_storage / "setup.sh")

    # ── Binary / media formats ───────────────────────────────────────────────
    files["png"] = make_png_file(hot_storage / "image.png")
    files["jpg"] = make_jpeg_file(hot_storage / "photo.jpg")
    files["pdf"] = make_pdf_file(hot_storage / "report.pdf")
    files["zip"] = make_zip_file(hot_storage / "archive.zip")
    files["bin"] = make_binary_file(hot_storage / "data.bin", 2048)

    # ── Size extremes ────────────────────────────────────────────────────────
    files["empty"] = make_empty_file(hot_storage / "empty.dat")
    files["small"] = make_small_file(hot_storage / "tiny.dat", 512)
    files["large"] = make_large_file(hot_storage / "big.bin", 2)  # 2 MiB

    # ── Case-variation file names ────────────────────────────────────────────
    files["upper"] = make_text_file(hot_storage / "REPORT.TXT", "Upper-case extension")
    files["mixed"] = make_text_file(hot_storage / "Report_Final_v2.TXT", "Mixed-case name")

    # ── Files in nested sub-directory ───────────────────────────────────────
    sub = hot_storage / "subdir"
    sub.mkdir()
    files["nested_txt"] = make_text_file(sub / "nested.txt", "Nested text file")
    files["nested_log"] = make_log_file(sub / "nested.log")
    files["nested_json"] = make_json_file(sub / "nested.json")

    return files


@pytest.fixture
def storage_setup(db_session, hot_storage, cold_storage):
    """
    Create MonitoredPath + ColdStorageLocation in the test DB.

    Returns (monitored_path, hot_path, cold_path).
    """
    cold_loc = ColdStorageLocation(name="TestCold", path=str(cold_storage))
    db_session.add(cold_loc)
    db_session.flush()

    mp = MonitoredPath(
        name="TestPath",
        source_path=str(hot_storage),
        operation_type=OperationType.MOVE,
        enabled=True,
        check_interval_seconds=3600,
    )
    mp.storage_locations.append(cold_loc)
    db_session.add(mp)
    db_session.commit()
    db_session.refresh(mp)
    return mp, hot_storage, cold_storage


# ==============================================================================
# Helpers
# ==============================================================================


def _age_file(path: Path, minutes: float) -> None:
    """Artificially set a file's mtime/atime to `minutes` ago."""
    t = time.time() - (minutes * 60)
    os.utime(str(path), (t, t))


def _make_inventory(db, path_id: int, file_path: Path, storage_type=StorageType.HOT) -> FileInventory:
    """Insert a FileInventory record so the workflow service can lock it."""
    st = file_path.stat()
    entry = FileInventory(
        path_id=path_id,
        file_path=str(file_path),
        storage_type=storage_type,
        file_size=st.st_size,
        file_mtime=__import__("datetime").datetime.fromtimestamp(
            st.st_mtime, tz=__import__("datetime").timezone.utc
        ),
        status=FileStatus.ACTIVE,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ==============================================================================
# Part 1 – CriteriaMatcher unit tests (all CriterionTypes × all Operators)
# ==============================================================================


class TestMtimeCriteria:
    """MTIME – file age in minutes based on modification time."""

    @pytest.mark.parametrize(
        "age_min, op, value, expected",
        [
            # GT: keep if older than N minutes
            (120, Operator.GT, "60", True),   # 2 h old > 60 min → keep
            (30, Operator.GT, "60", False),   # 30 min old > 60 min → False → cold
            # LT: keep if newer than N minutes
            (5, Operator.LT, "60", True),     # 5 min old < 60 min → keep
            (120, Operator.LT, "60", False),  # 2 h old < 60 min → False → cold
            # GTE: keep if age >= N  (use 65 min old ≥ 60 to avoid boundary timing drift)
            (65, Operator.GTE, "60", True),
            (55, Operator.GTE, "60", False),
            # LTE: keep if age <= N  (use 55 min old ≤ 60 to avoid boundary timing drift)
            (55, Operator.LTE, "60", True),
            (65, Operator.LTE, "60", False),
            # EQ: keep if age ≈ N (within 0.5 min tolerance)
            (30, Operator.EQ, "30", True),
            (30, Operator.EQ, "31", False),
        ],
    )
    def test_mtime_operators(self, hot_storage, make_criterion, age_min, op, value, expected):
        f = make_text_file(hot_storage / "file.txt")
        _age_file(f, age_min)
        c = make_criterion(CriterionType.MTIME, op, value)
        result, ids = CriteriaMatcher.match_file(f, [c])
        assert result is expected

    def test_mtime_with_log_file(self, hot_storage, make_criterion):
        """Real log file aged 90 minutes should not satisfy 'keep if mtime < 60'."""
        f = make_log_file(hot_storage / "old.log")
        _age_file(f, 90)
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False  # → move to cold

    def test_mtime_with_json_file(self, hot_storage, make_criterion):
        """Fresh JSON config (just written) satisfies 'keep if mtime < 60'."""
        f = make_json_file(hot_storage / "config.json")
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True  # → stay in hot

    def test_mtime_with_binary_file(self, hot_storage, make_criterion):
        """Binary file aged 2 h satisfies 'keep if mtime > 60'."""
        f = make_binary_file(hot_storage / "data.bin", 1024)
        _age_file(f, 120)
        c = make_criterion(CriterionType.MTIME, Operator.GT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_mtime_invalid_value_returns_false(self, hot_storage, make_criterion):
        """Non-numeric criterion value should not match."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.MTIME, Operator.GT, "not_a_number")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False


class TestAtimeCriteria:
    """ATIME – file age in minutes based on access time (Linux only in these tests)."""

    @patch("platform.system", return_value="Linux")
    @pytest.mark.parametrize(
        "age_min, op, value, expected",
        [
            (10, Operator.GT, "5", True),
            (3, Operator.GT, "5", False),
            (3, Operator.LT, "5", True),
            (10, Operator.LT, "5", False),
            # GTE/LTE: avoid exact boundary; use ±5 min margin
            (10, Operator.GTE, "5", True),
            (3, Operator.GTE, "5", False),
            (3, Operator.LTE, "5", True),
            (10, Operator.LTE, "5", False),
        ],
    )
    def test_atime_operators(self, mock_platform, hot_storage, make_criterion, age_min, op, value, expected):
        f = make_text_file(hot_storage / "file.txt")
        _age_file(f, age_min)
        c = make_criterion(CriterionType.ATIME, op, value)
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is expected

    @patch("platform.system", return_value="Linux")
    def test_atime_with_png(self, mock_platform, hot_storage, make_criterion):
        """PNG file: atime 30 min ago should not satisfy 'keep if atime < 10'."""
        f = make_png_file(hot_storage / "image.png")
        _age_file(f, 30)
        c = make_criterion(CriterionType.ATIME, Operator.LT, "10")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    @patch("platform.system", return_value="Darwin")
    @patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
    def test_atime_macos_uses_last_open_when_newer(
        self, mock_get_last_open, mock_platform, hot_storage, make_criterion
    ):
        """On macOS, Last Open time supersedes atime when more recent."""
        now = time.time()
        f = make_text_file(hot_storage / "file.txt")
        _age_file(f, 60)  # atime 60 min ago
        mock_get_last_open.return_value = now - (5 * 60)  # last open 5 min ago

        c = make_criterion(CriterionType.ATIME, Operator.LT, "10")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True  # 5 min < 10 min → keep

    @patch("platform.system", return_value="Darwin")
    @patch("app.services.criteria_matcher.CriteriaMatcher._get_macos_last_open_time")
    def test_atime_macos_never_opened_treated_as_epoch(
        self, mock_get_last_open, mock_platform, hot_storage, make_criterion
    ):
        """On macOS, if Last Open returns None, file is treated as infinitely old."""
        f = make_text_file(hot_storage / "file.txt")
        mock_get_last_open.return_value = None  # never opened

        c = make_criterion(CriterionType.ATIME, Operator.LT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        # epoch age is enormous → NOT < 60 → False → move to cold
        assert result is False


class TestCtimeCriteria:
    """CTIME – file age in minutes based on metadata-change time.

    Note: On Linux, ``st_ctime`` is the *inode-change* time, not creation time.
    Calling ``os.utime`` to set mtime also resets ctime to "now", so we cannot
    artificially age a file's ctime via os.utime.  Instead we test against a
    freshly-created file whose ctime is "just now" (age ≈ 0 minutes).
    """

    def test_ctime_fresh_file_lt_passes(self, hot_storage, make_criterion):
        """Fresh file: ctime ≈ 0 → satisfies 'ctime < 5' (keep in hot)."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.CTIME, Operator.LT, "5")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_ctime_fresh_file_gt_fails(self, hot_storage, make_criterion):
        """Fresh file: ctime ≈ 0 → does NOT satisfy 'ctime > 60' (→ cold)."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.CTIME, Operator.GT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_ctime_fresh_file_gte_large_fails(self, hot_storage, make_criterion):
        """Fresh file ctime age ≈ 0 does NOT satisfy 'ctime >= 60'."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.CTIME, Operator.GTE, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_ctime_fresh_file_lte_large_passes(self, hot_storage, make_criterion):
        """Fresh file ctime age ≈ 0 satisfies 'ctime <= 60'."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.CTIME, Operator.LTE, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_ctime_with_log_file(self, hot_storage, make_criterion):
        """Log file just written: ctime < 60 → keep in hot."""
        f = make_log_file(hot_storage / "app.log")
        c = make_criterion(CriterionType.CTIME, Operator.LT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_ctime_with_png_file(self, hot_storage, make_criterion):
        """PNG file just created: ctime age ≈ 0 satisfies 'ctime < 5'."""
        f = make_png_file(hot_storage / "img.png")
        c = make_criterion(CriterionType.CTIME, Operator.LT, "5")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True


class TestSizeCriteria:
    """SIZE – file size comparisons with unit suffixes."""

    @pytest.mark.parametrize(
        "file_size, op, value, expected",
        [
            # Bytes (no suffix or 'c')
            (2000, Operator.GT, "1000c", True),
            (500, Operator.GT, "1000c", False),
            (1000, Operator.EQ, "1000c", True),
            (999, Operator.EQ, "1000c", False),
            # Kilobytes (k/K)
            (2048, Operator.GT, "1k", True),
            (512, Operator.GT, "1k", False),
            (1024, Operator.EQ, "1k", True),
            (1023, Operator.LT, "1k", True),
            (1025, Operator.LT, "1k", False),
            (1024, Operator.GTE, "1k", True),
            (1024, Operator.LTE, "1k", True),
            (1025, Operator.LTE, "1k", False),
            # Megabytes (m/M)
            (2 * 1024 * 1024, Operator.GT, "1M", True),
            (512 * 1024, Operator.LT, "1M", True),
            (1024 * 1024, Operator.EQ, "1M", True),
            (1024 * 1024 - 1, Operator.EQ, "1M", False),
            # Gigabytes (g/G)
            (2 * 1024 * 1024 * 1024, Operator.GT, "1G", True),
            (1024 * 1024 * 1024, Operator.EQ, "1G", True),
            (1024 * 1024 * 1024 - 1, Operator.LT, "1G", True),
        ],
    )
    def test_size_operators(self, hot_storage, make_criterion, file_size, op, value, expected):
        f = hot_storage / "file.dat"
        f.write_bytes(b"\x00" * file_size)
        c = make_criterion(CriterionType.SIZE, op, value)
        stat_info = f.stat()
        result = CriteriaMatcher._match_size(stat_info.st_size, op, value)
        assert result is expected

    def test_size_with_empty_file(self, hot_storage, make_criterion):
        """Empty file: size 0 bytes, should not satisfy 'size > 0'."""
        f = make_empty_file(hot_storage / "empty.dat")
        c = make_criterion(CriterionType.SIZE, Operator.GT, "0c")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_size_with_large_file(self, hot_storage, make_criterion):
        """2 MiB file satisfies 'size > 1M'."""
        f = make_large_file(hot_storage / "big.bin", 2)
        c = make_criterion(CriterionType.SIZE, Operator.GT, "1M")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_size_with_pdf(self, hot_storage, make_criterion):
        """PDF file (small) should not satisfy 'size > 1M'."""
        f = make_pdf_file(hot_storage / "report.pdf")
        c = make_criterion(CriterionType.SIZE, Operator.GT, "1M")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_size_invalid_value(self, hot_storage, make_criterion):
        """Garbage size value should return False (not crash)."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.SIZE, Operator.GT, "not_a_size")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False


class TestNameCriteria:
    """NAME – case-sensitive filename matching."""

    @pytest.mark.parametrize(
        "filename, op, value, expected",
        [
            # EQ: exact match
            ("report.pdf", Operator.EQ, "report.pdf", True),
            ("Report.pdf", Operator.EQ, "report.pdf", False),
            ("report.txt", Operator.EQ, "report.pdf", False),
            # CONTAINS: substring
            ("error_2024_01.log", Operator.CONTAINS, "2024", True),
            ("error_2024_01.log", Operator.CONTAINS, "2025", False),
            ("README.md", Operator.CONTAINS, "readme", False),  # case-sensitive
            # MATCHES: glob pattern
            ("backup_2024_01_15.tar", Operator.MATCHES, "backup_*.tar", True),
            ("backup_2024_01_15.tar.gz", Operator.MATCHES, "backup_*.tar", False),
            ("photo.JPG", Operator.MATCHES, "*.jpg", False),  # case-sensitive
            ("data_01.csv", Operator.MATCHES, "data_??.csv", True),
            ("data_001.csv", Operator.MATCHES, "data_??.csv", False),
            # REGEX: regular expression
            ("document_v3_final.pdf", Operator.REGEX, r"_v\d+_", True),
            ("document_final.pdf", Operator.REGEX, r"_v\d+_", False),
            ("report.PDF", Operator.REGEX, r"\.(pdf|PDF)$", True),
            ("report.doc", Operator.REGEX, r"\.(pdf|PDF)$", False),
        ],
    )
    def test_name_operators(self, hot_storage, make_criterion, filename, op, value, expected):
        f = make_text_file(hot_storage / filename)
        c = make_criterion(CriterionType.NAME, op, value)
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is expected

    def test_name_with_log_file(self, hot_storage, make_criterion):
        """'application.log' matches NAME contains 'application'."""
        f = make_log_file(hot_storage / "application.log")
        c = make_criterion(CriterionType.NAME, Operator.CONTAINS, "application")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_name_with_nested_file(self, hot_storage, make_criterion):
        """NAME criterion uses only the filename, not the full path."""
        sub = hot_storage / "subdir"
        sub.mkdir()
        f = make_text_file(sub / "report.txt")
        # "subdir" is NOT in the filename
        c = make_criterion(CriterionType.NAME, Operator.CONTAINS, "subdir")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_name_invalid_regex(self, hot_storage, make_criterion):
        """Invalid regex pattern should return False, not raise."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.NAME, Operator.REGEX, r"[invalid(")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False


class TestInameCriteria:
    """INAME – case-insensitive filename matching."""

    @pytest.mark.parametrize(
        "filename, op, value, expected",
        [
            # EQ: case-insensitive exact
            ("Report.PDF", Operator.EQ, "report.pdf", True),
            ("REPORT.PDF", Operator.EQ, "report.pdf", True),
            ("report.pdf", Operator.EQ, "report.pdf", True),
            ("report.doc", Operator.EQ, "report.pdf", False),
            # CONTAINS: case-insensitive substring
            ("ERROR_2024.LOG", Operator.CONTAINS, "error", True),
            ("error_2024.log", Operator.CONTAINS, "ERROR", True),
            ("warning.log", Operator.CONTAINS, "error", False),
            # MATCHES: case-insensitive glob
            ("Photo.JPG", Operator.MATCHES, "*.jpg", True),
            ("photo.jpeg", Operator.MATCHES, "*.JPG", False),  # INAME lowercases both → "jpeg" vs "jpg"
            ("BACKUP_01.TAR", Operator.MATCHES, "backup_*.tar", True),
            # REGEX: case-insensitive regex
            ("Document_Final.PDF", Operator.REGEX, r"\.(pdf|doc)$", True),
            ("spreadsheet.xlsx", Operator.REGEX, r"\.(pdf|doc)$", False),
        ],
    )
    def test_iname_operators(self, hot_storage, make_criterion, filename, op, value, expected):
        f = make_text_file(hot_storage / filename)
        c = make_criterion(CriterionType.INAME, op, value)
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is expected

    def test_iname_with_png(self, hot_storage, make_criterion):
        """PNG file with uppercase extension matches INAME EQ 'image.png'."""
        f = make_png_file(hot_storage / "IMAGE.PNG")
        c = make_criterion(CriterionType.INAME, Operator.EQ, "image.png")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True


class TestTypeCriteria:
    """TYPE – file type matching (f=file, d=directory, l=symlink)."""

    def test_type_regular_file(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "f")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_type_file_alias(self, hot_storage, make_criterion):
        """'file' is a valid alias for 'f'."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "file")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_type_directory(self, hot_storage, make_criterion):
        d = hot_storage / "mydir"
        d.mkdir()
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "d")
        result, _ = CriteriaMatcher.match_file(d, [c])
        assert result is True

    def test_type_directory_alias(self, hot_storage, make_criterion):
        d = hot_storage / "mydir"
        d.mkdir()
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "directory")
        result, _ = CriteriaMatcher.match_file(d, [c])
        assert result is True

    def test_type_symlink(self, hot_storage, make_criterion):
        target = make_text_file(hot_storage / "target.txt")
        link = hot_storage / "link.txt"
        link.symlink_to(target)
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "l")
        # match_file follows symlinks for stat; _match_type checks is_symlink()
        assert CriteriaMatcher._match_type(link, link.lstat(), "l") is True

    def test_type_regular_file_not_directory(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "d")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_type_with_many_file_formats(self, hot_storage, make_criterion):
        """All real file types should match TYPE=f."""
        creators = [make_png_file, make_pdf_file, make_zip_file, make_json_file, make_log_file]
        names = ["img.png", "doc.pdf", "arch.zip", "cfg.json", "app.log"]
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "f")
        for creator, name in zip(creators, names):
            f = creator(hot_storage / name)
            result, _ = CriteriaMatcher.match_file(f, [c])
            assert result is True, f"{name} should match TYPE=f"

    def test_type_unknown_value(self, hot_storage, make_criterion):
        """Unknown type value should return False."""
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "x")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False


class TestPermCriteria:
    """PERM – permission matching (octal and symbolic)."""

    def test_perm_octal_exact_match(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        f.chmod(0o644)
        c = make_criterion(CriterionType.PERM, Operator.EQ, "644")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_perm_octal_no_match(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        f.chmod(0o644)
        c = make_criterion(CriterionType.PERM, Operator.EQ, "755")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_perm_executable_script(self, hot_storage, make_criterion):
        f = make_shell_script(hot_storage / "run.sh")  # chmod 755 inside
        c = make_criterion(CriterionType.PERM, Operator.EQ, "755")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_perm_symbolic_readable(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        f.chmod(0o644)
        c = make_criterion(CriterionType.PERM, Operator.EQ, "r")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_perm_symbolic_not_executable(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        f.chmod(0o644)  # not executable
        c = make_criterion(CriterionType.PERM, Operator.EQ, "x")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_perm_with_png(self, hot_storage, make_criterion):
        f = make_png_file(hot_storage / "image.png")
        f.chmod(0o600)
        c = make_criterion(CriterionType.PERM, Operator.EQ, "600")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True


class TestUserCriteria:
    """USER – match file owner by name or numeric UID."""

    def test_user_by_uid(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        uid = f.stat().st_uid
        c = make_criterion(CriterionType.USER, Operator.EQ, str(uid))
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_user_wrong_uid(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        uid = f.stat().st_uid
        wrong_uid = uid + 99999
        c = make_criterion(CriterionType.USER, Operator.EQ, str(wrong_uid))
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_user_by_name(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        uid = f.stat().st_uid
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            pytest.skip("Current user not in passwd database")
        c = make_criterion(CriterionType.USER, Operator.EQ, username)
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_user_nonexistent_name(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        c = make_criterion(CriterionType.USER, Operator.EQ, "definitely_not_a_real_user_xyz")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False


class TestGroupCriteria:
    """GROUP – match file group by name or numeric GID."""

    def test_group_by_gid(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        gid = f.stat().st_gid
        c = make_criterion(CriterionType.GROUP, Operator.EQ, str(gid))
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_group_wrong_gid(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        gid = f.stat().st_gid
        wrong_gid = gid + 99999
        c = make_criterion(CriterionType.GROUP, Operator.EQ, str(wrong_gid))
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    def test_group_by_name(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "file.txt")
        gid = f.stat().st_gid
        try:
            groupname = grp.getgrgid(gid).gr_name
        except KeyError:
            pytest.skip("Current group not in group database")
        c = make_criterion(CriterionType.GROUP, Operator.EQ, groupname)
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True


# ==============================================================================
# Part 2 – Multi-criteria AND logic
# ==============================================================================


class TestMultipleCriteria:
    """All enabled criteria must match for a file to stay in hot storage."""

    def test_all_criteria_match(self, hot_storage, make_criterion):
        """File matches MTIME + SIZE + NAME → True (keep in hot)."""
        f = make_log_file(hot_storage / "app_2024.log")
        f.write_bytes(b"x" * 2048)  # overwrite to set size = 2 KiB
        # Criteria: mtime < 60 min, size > 1k, name contains 'app'
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.LT, "60"),
            make_criterion(CriterionType.SIZE, Operator.GT, "1k"),
            make_criterion(CriterionType.NAME, Operator.CONTAINS, "app"),
        ]
        result, matched_ids = CriteriaMatcher.match_file(f, criteria)
        assert result is True
        assert len(matched_ids) == 3

    def test_one_criterion_fails(self, hot_storage, make_criterion):
        """If one criterion fails, match_file returns False immediately."""
        f = make_text_file(hot_storage / "tiny.txt", "Hi")
        # SIZE > 1M will fail for a tiny file
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.LT, "60"),  # True
            make_criterion(CriterionType.SIZE, Operator.GT, "1M"),   # False → short-circuit
            make_criterion(CriterionType.NAME, Operator.EQ, "tiny.txt"),  # would be True
        ]
        result, matched_ids = CriteriaMatcher.match_file(f, criteria)
        assert result is False
        assert matched_ids == []

    def test_mtime_and_name_pattern(self, hot_storage, make_criterion):
        """Old CSV file should match both 'mtime > 30' and 'name matches *.csv'."""
        f = make_csv_file(hot_storage / "export.csv")
        _age_file(f, 60)
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.GT, "30"),
            make_criterion(CriterionType.NAME, Operator.MATCHES, "*.csv"),
        ]
        result, _ = CriteriaMatcher.match_file(f, criteria)
        assert result is True

    def test_size_and_type(self, hot_storage, make_criterion):
        """Large binary file matching size > 1M AND type = f."""
        f = make_large_file(hot_storage / "big.bin", 2)
        criteria = [
            make_criterion(CriterionType.SIZE, Operator.GT, "1M"),
            make_criterion(CriterionType.TYPE, Operator.EQ, "f"),
        ]
        result, _ = CriteriaMatcher.match_file(f, criteria)
        assert result is True

    def test_no_criteria_keeps_file_hot(self, hot_storage):
        """Empty criteria list → file stays in hot storage."""
        f = make_text_file(hot_storage / "file.txt")
        result, ids = CriteriaMatcher.match_file(f, [])
        assert result is True
        assert ids == []

    def test_all_disabled_criteria(self, hot_storage, make_criterion):
        """Disabled criteria are ignored → file stays in hot."""
        f = make_large_file(hot_storage / "big.bin", 2)
        criteria = [
            make_criterion(CriterionType.SIZE, Operator.LT, "1c", enabled=False),
            make_criterion(CriterionType.MTIME, Operator.GT, "999999", enabled=False),
        ]
        result, ids = CriteriaMatcher.match_file(f, criteria)
        assert result is True
        assert ids == []

    def test_mix_of_enabled_disabled(self, hot_storage, make_criterion):
        """Only enabled criteria are evaluated."""
        f = make_text_file(hot_storage / "file.txt")
        criteria = [
            # Enabled criterion that matches
            make_criterion(CriterionType.MTIME, Operator.LT, "60", enabled=True),
            # Disabled criterion that would fail (size > 100 MB impossible)
            make_criterion(CriterionType.SIZE, Operator.GT, "100M", enabled=False),
        ]
        result, _ = CriteriaMatcher.match_file(f, criteria)
        assert result is True  # only enabled one evaluated, and it matches


# ==============================================================================
# Part 3 – Edge cases and error handling
# ==============================================================================


class TestEdgeCases:
    """Boundary conditions and error-resilience."""

    def test_nonexistent_file(self, hot_storage, make_criterion):
        """Trying to match a file that doesn't exist returns False."""
        ghost = hot_storage / "ghost.txt"
        c = make_criterion(CriterionType.NAME, Operator.EQ, "ghost.txt")
        result, _ = CriteriaMatcher.match_file(ghost, [c])
        assert result is False

    def test_zero_byte_file_size_eq(self, hot_storage, make_criterion):
        f = make_empty_file(hot_storage / "empty.dat")
        c = make_criterion(CriterionType.SIZE, Operator.EQ, "0c")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_file_at_exact_time_boundary(self, hot_storage, make_criterion):
        """File exactly at criterion time boundary with EQ operator."""
        f = make_text_file(hot_storage / "file.txt")
        _age_file(f, 30)
        c = make_criterion(CriterionType.MTIME, Operator.EQ, "30")
        result, _ = CriteriaMatcher.match_file(f, [c])
        # EQ has ±0.5 min tolerance, 30 min matches 30 → True
        assert result is True

    def test_many_file_types_with_name_glob(self, hot_storage, make_criterion):
        """Glob pattern selects a subset of diverse files."""
        make_log_file(hot_storage / "app.log")
        make_log_file(hot_storage / "error.log")
        make_text_file(hot_storage / "notes.txt")
        make_json_file(hot_storage / "config.json")

        log_criterion = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.log")

        for fname in ["app.log", "error.log"]:
            f = hot_storage / fname
            result, _ = CriteriaMatcher.match_file(f, [log_criterion])
            assert result is True, f"{fname} should match *.log"

        for fname in ["notes.txt", "config.json"]:
            f = hot_storage / fname
            result, _ = CriteriaMatcher.match_file(f, [log_criterion])
            assert result is False, f"{fname} should NOT match *.log"

    def test_symlink_metadata_from_actual_target(self, hot_storage, make_criterion):
        """match_file follows symlinks; criteria see the target file's metadata."""
        target = make_large_file(hot_storage / "big_target.bin", 2)
        link = hot_storage / "link_to_big.bin"
        link.symlink_to(target)

        # SIZE > 1M should match (target is 2 MiB)
        c = make_criterion(CriterionType.SIZE, Operator.GT, "1M")
        result, _ = CriteriaMatcher.match_file(link, [c], actual_file_path=target)
        assert result is True

    def test_criteria_matched_ids_returned(self, hot_storage, make_criterion):
        """Returned matched_ids list contains IDs of all matching criteria."""
        f = make_binary_file(hot_storage / "data.bin", 4096)
        criteria = [
            make_criterion(CriterionType.SIZE, Operator.GT, "1k"),      # id 1
            make_criterion(CriterionType.MTIME, Operator.LT, "60"),      # id 2
            make_criterion(CriterionType.TYPE, Operator.EQ, "f"),         # id 3
        ]
        result, ids = CriteriaMatcher.match_file(f, criteria)
        assert result is True
        assert sorted(ids) == [1, 2, 3]


# ==============================================================================
# Part 4 – Many file types: each common type tested across multiple criteria
# ==============================================================================


class TestFileTypeSuite:
    """
    Comprehensive coverage: each file format is exercised against
    the most relevant criteria types.
    """

    # ── Text file ─────────────────────────────────────────────────────────────

    def test_text_file_name_eq(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "notes.txt")
        c = make_criterion(CriterionType.NAME, Operator.EQ, "notes.txt")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_text_file_mtime_recent(self, hot_storage, make_criterion):
        f = make_text_file(hot_storage / "notes.txt")
        c = make_criterion(CriterionType.MTIME, Operator.LT, "5")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True  # just created → stay hot

    # ── Log file ──────────────────────────────────────────────────────────────

    def test_log_file_name_matches_glob(self, hot_storage, make_criterion):
        f = make_log_file(hot_storage / "access.log")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.log")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_log_file_old_goes_cold(self, hot_storage, make_criterion):
        """Log aged 2 days should not satisfy mtime < 60."""
        f = make_log_file(hot_storage / "old.log")
        _age_file(f, 60 * 24 * 2)  # 2 days
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    # ── JSON file ─────────────────────────────────────────────────────────────

    def test_json_file_name_regex(self, hot_storage, make_criterion):
        f = make_json_file(hot_storage / "settings_v2.json")
        c = make_criterion(CriterionType.NAME, Operator.REGEX, r"settings.*\.json$")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_json_file_type_is_file(self, hot_storage, make_criterion):
        f = make_json_file(hot_storage / "config.json")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "f")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── CSV file ──────────────────────────────────────────────────────────────

    def test_csv_file_size_small(self, hot_storage, make_criterion):
        f = make_csv_file(hot_storage / "data.csv")
        c = make_criterion(CriterionType.SIZE, Operator.LT, "10k")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_csv_file_iname_case_insensitive(self, hot_storage, make_criterion):
        f = make_csv_file(hot_storage / "EXPORT.CSV")
        c = make_criterion(CriterionType.INAME, Operator.MATCHES, "*.csv")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── PDF file ──────────────────────────────────────────────────────────────

    def test_pdf_file_name_contains(self, hot_storage, make_criterion):
        f = make_pdf_file(hot_storage / "annual_report_2024.pdf")
        c = make_criterion(CriterionType.NAME, Operator.CONTAINS, "report")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_pdf_file_small_size(self, hot_storage, make_criterion):
        f = make_pdf_file(hot_storage / "report.pdf")
        c = make_criterion(CriterionType.SIZE, Operator.LT, "1M")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── PNG image ─────────────────────────────────────────────────────────────

    def test_png_iname_glob(self, hot_storage, make_criterion):
        f = make_png_file(hot_storage / "Screenshot.PNG")
        c = make_criterion(CriterionType.INAME, Operator.MATCHES, "*.png")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_png_type_file(self, hot_storage, make_criterion):
        f = make_png_file(hot_storage / "image.png")
        c = make_criterion(CriterionType.TYPE, Operator.EQ, "f")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── JPEG image ────────────────────────────────────────────────────────────

    def test_jpeg_iname_jpg_extension(self, hot_storage, make_criterion):
        f = make_jpeg_file(hot_storage / "photo.JPG")
        c = make_criterion(CriterionType.INAME, Operator.EQ, "photo.jpg")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── ZIP archive ───────────────────────────────────────────────────────────

    def test_zip_file_name_matches(self, hot_storage, make_criterion):
        f = make_zip_file(hot_storage / "backup_20240115.zip")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "backup_*.zip")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_zip_file_size_gte(self, hot_storage, make_criterion):
        f = make_zip_file(hot_storage / "archive.zip")
        size = f.stat().st_size
        c = make_criterion(CriterionType.SIZE, Operator.GTE, str(size))
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── Shell script ──────────────────────────────────────────────────────────

    def test_script_executable_perm(self, hot_storage, make_criterion):
        f = make_shell_script(hot_storage / "deploy.sh")
        c = make_criterion(CriterionType.PERM, Operator.EQ, "755")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_script_name_sh(self, hot_storage, make_criterion):
        f = make_shell_script(hot_storage / "setup.sh")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.sh")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    # ── Large binary ──────────────────────────────────────────────────────────

    def test_large_binary_size_gt_1m(self, hot_storage, make_criterion):
        f = make_large_file(hot_storage / "big.bin", 2)
        c = make_criterion(CriterionType.SIZE, Operator.GT, "1M")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_large_binary_mtime_and_size(self, hot_storage, make_criterion):
        """2 MiB binary aged 3 h matches mtime > 120 AND size > 1M."""
        f = make_large_file(hot_storage / "big.bin", 2)
        _age_file(f, 180)
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.GT, "120"),
            make_criterion(CriterionType.SIZE, Operator.GT, "1M"),
        ]
        result, _ = CriteriaMatcher.match_file(f, criteria)
        assert result is True

    # ── Empty file ────────────────────────────────────────────────────────────

    def test_empty_file_size_eq_zero(self, hot_storage, make_criterion):
        f = make_empty_file(hot_storage / "placeholder.dat")
        c = make_criterion(CriterionType.SIZE, Operator.EQ, "0c")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_empty_file_size_not_gt_1k(self, hot_storage, make_criterion):
        f = make_empty_file(hot_storage / "placeholder.dat")
        c = make_criterion(CriterionType.SIZE, Operator.GT, "1k")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is False

    # ── Nested file ───────────────────────────────────────────────────────────

    def test_nested_file_matched_by_name(self, hot_storage, make_criterion):
        sub = hot_storage / "archive" / "2024"
        sub.mkdir(parents=True)
        f = make_text_file(sub / "summary.txt")
        c = make_criterion(CriterionType.NAME, Operator.EQ, "summary.txt")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True

    def test_nested_log_old_mtime(self, hot_storage, make_criterion):
        sub = hot_storage / "logs" / "old"
        sub.mkdir(parents=True)
        f = make_log_file(sub / "archive.log")
        _age_file(f, 60 * 24 * 7)  # 7 days old
        c = make_criterion(CriterionType.MTIME, Operator.GT, "60")
        result, _ = CriteriaMatcher.match_file(f, [c])
        assert result is True


# ==============================================================================
# Part 5 – Integration: real file movement using FileMover directly
# ==============================================================================


class TestFileMoverIntegration:
    """
    Tests actual file movement between hot and cold storage using
    ``FileMover.move_with_rollback`` directly. This avoids the DB-session
    complications of ``_process_single_file`` while still verifying that
    diverse file types are correctly moved/copied/symlinked.
    """

    from app.services.file_mover import move_with_rollback as _mwr  # imported at class scope

    # ── MOVE operation ────────────────────────────────────────────────────────

    def test_move_text_file(self, hot_storage, cold_storage):
        """Text file moved; hot copy gone, cold copy has identical content."""
        from app.services.file_mover import move_with_rollback

        f = make_text_file(hot_storage / "transfer.txt", "Transfer me")
        dest = cold_storage / "transfer.txt"
        success, error, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert not f.exists()
        assert dest.read_text() == "Transfer me"

    def test_move_json_file_preserves_content(self, hot_storage, cold_storage):
        """JSON moved; content is byte-identical."""
        import json
        from app.services.file_mover import move_with_rollback

        f = make_json_file(hot_storage / "settings.json")
        expected = json.loads(f.read_text())
        dest = cold_storage / "settings.json"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert json.loads(dest.read_text()) == expected

    def test_move_binary_file_byte_integrity(self, hot_storage, cold_storage):
        """Binary file moved; bytes are identical."""
        from app.services.file_mover import move_with_rollback

        f = make_binary_file(hot_storage / "data.bin", 4096)
        original = f.read_bytes()
        dest = cold_storage / "data.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_bytes() == original

    def test_move_png_file_byte_integrity(self, hot_storage, cold_storage):
        """PNG image moved; bytes are identical."""
        from app.services.file_mover import move_with_rollback

        f = make_png_file(hot_storage / "image.png")
        original = f.read_bytes()
        dest = cold_storage / "image.png"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_bytes() == original

    def test_move_jpeg_file(self, hot_storage, cold_storage):
        """JPEG moved; bytes are identical."""
        from app.services.file_mover import move_with_rollback

        f = make_jpeg_file(hot_storage / "photo.jpg")
        original = f.read_bytes()
        dest = cold_storage / "photo.jpg"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_bytes() == original

    def test_move_pdf_file(self, hot_storage, cold_storage):
        """PDF moved; original gone, cold copy intact."""
        from app.services.file_mover import move_with_rollback

        f = make_pdf_file(hot_storage / "report.pdf")
        original = f.read_bytes()
        dest = cold_storage / "report.pdf"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert not f.exists()
        assert dest.read_bytes() == original

    def test_move_zip_file(self, hot_storage, cold_storage):
        """ZIP archive moved; bytes identical."""
        from app.services.file_mover import move_with_rollback

        f = make_zip_file(hot_storage / "archive.zip")
        original = f.read_bytes()
        dest = cold_storage / "archive.zip"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_bytes() == original

    def test_move_large_file(self, hot_storage, cold_storage):
        """2 MiB file moved; size preserved."""
        from app.services.file_mover import move_with_rollback

        f = make_large_file(hot_storage / "big.bin", 2)
        dest = cold_storage / "big.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.stat().st_size == 2 * 1024 * 1024

    def test_move_empty_file(self, hot_storage, cold_storage):
        """Empty file moved; cold copy exists with 0 bytes."""
        from app.services.file_mover import move_with_rollback

        f = make_empty_file(hot_storage / "placeholder.dat")
        dest = cold_storage / "placeholder.dat"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.exists()
        assert dest.stat().st_size == 0

    def test_move_log_file(self, hot_storage, cold_storage):
        """Log file moved; content preserved."""
        from app.services.file_mover import move_with_rollback

        f = make_log_file(hot_storage / "app.log")
        original = f.read_text()
        dest = cold_storage / "app.log"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_text() == original

    def test_move_csv_file(self, hot_storage, cold_storage):
        """CSV moved; content preserved."""
        from app.services.file_mover import move_with_rollback

        f = make_csv_file(hot_storage / "data.csv")
        original = f.read_text()
        dest = cold_storage / "data.csv"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_text() == original

    def test_move_xml_file(self, hot_storage, cold_storage):
        """XML file moved; content preserved."""
        from app.services.file_mover import move_with_rollback

        f = make_xml_file(hot_storage / "settings.xml")
        original = f.read_text()
        dest = cold_storage / "settings.xml"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert dest.read_text() == original

    def test_move_shell_script(self, hot_storage, cold_storage):
        """Shell script moved; executable permission preserved."""
        from app.services.file_mover import move_with_rollback

        f = make_shell_script(hot_storage / "setup.sh")
        dest = cold_storage / "setup.sh"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert bool(dest.stat().st_mode & stat.S_IXUSR)

    def test_move_preserves_directory_structure(self, hot_storage, cold_storage):
        """File in sub-directory moved; relative path preserved under cold root."""
        from app.services.file_mover import move_with_rollback, preserve_directory_structure

        sub = hot_storage / "year" / "month"
        sub.mkdir(parents=True)
        f = make_text_file(sub / "report.txt", "Monthly report")
        dest = preserve_directory_structure(f, hot_storage, cold_storage)
        dest.parent.mkdir(parents=True, exist_ok=True)
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert (cold_storage / "year" / "month" / "report.txt").read_text() == "Monthly report"

    # ── COPY operation ────────────────────────────────────────────────────────

    def test_copy_text_file_keeps_original(self, hot_storage, cold_storage):
        """COPY keeps hot file and creates an identical cold copy."""
        from app.services.file_mover import move_with_rollback

        f = make_text_file(hot_storage / "source.txt", "Original content")
        dest = cold_storage / "source.txt"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert f.exists()  # original retained in hot
        assert dest.read_text() == "Original content"

    def test_copy_binary_file(self, hot_storage, cold_storage):
        """COPY of binary file; both copies are byte-identical."""
        from app.services.file_mover import move_with_rollback

        f = make_binary_file(hot_storage / "data.bin", 2048)
        original = f.read_bytes()
        dest = cold_storage / "data.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert f.read_bytes() == original
        assert dest.read_bytes() == original

    def test_copy_png_file(self, hot_storage, cold_storage):
        """COPY of PNG; both copies are byte-identical."""
        from app.services.file_mover import move_with_rollback

        f = make_png_file(hot_storage / "photo.png")
        original = f.read_bytes()
        dest = cold_storage / "photo.png"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert f.read_bytes() == original
        assert dest.read_bytes() == original

    def test_copy_large_file(self, hot_storage, cold_storage):
        """COPY of 2 MiB file; size preserved, original intact."""
        from app.services.file_mover import move_with_rollback

        f = make_large_file(hot_storage / "big.bin", 2)
        dest = cold_storage / "big.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert f.stat().st_size == 2 * 1024 * 1024
        assert dest.stat().st_size == 2 * 1024 * 1024

    def test_copy_empty_file(self, hot_storage, cold_storage):
        """COPY of empty file."""
        from app.services.file_mover import move_with_rollback

        f = make_empty_file(hot_storage / "empty.dat")
        dest = cold_storage / "empty.dat"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert f.exists()
        assert dest.stat().st_size == 0

    # ── SYMLINK operation ─────────────────────────────────────────────────────

    def test_symlink_creates_link_in_hot(self, hot_storage, cold_storage):
        """SYMLINK moves file to cold and leaves a symlink at the original hot path."""
        from app.services.file_mover import move_with_rollback

        f = make_text_file(hot_storage / "linked.txt", "Linked content")
        dest = cold_storage / "linked.txt"
        success, _, _ = move_with_rollback(f, dest, OperationType.SYMLINK)
        assert success is True
        assert f.is_symlink()
        assert dest.exists()
        assert f.read_text() == "Linked content"  # readable via symlink

    def test_symlink_with_pdf(self, hot_storage, cold_storage):
        """SYMLINK for a PDF file; symlink resolves correctly."""
        from app.services.file_mover import move_with_rollback

        f = make_pdf_file(hot_storage / "doc.pdf")
        original = f.read_bytes()
        dest = cold_storage / "doc.pdf"
        success, _, _ = move_with_rollback(f, dest, OperationType.SYMLINK)
        assert success is True
        assert f.is_symlink()
        assert f.read_bytes() == original

    def test_symlink_with_binary(self, hot_storage, cold_storage):
        """SYMLINK for a binary file."""
        from app.services.file_mover import move_with_rollback

        f = make_binary_file(hot_storage / "data.bin", 1024)
        original = f.read_bytes()
        dest = cold_storage / "data.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.SYMLINK)
        assert success is True
        assert f.is_symlink()
        assert f.read_bytes() == original

    # ── Timestamp preservation ────────────────────────────────────────────────

    def test_move_preserves_mtime(self, hot_storage, cold_storage):
        """Moved file retains its original mtime."""
        from app.services.file_mover import move_with_rollback

        f = make_text_file(hot_storage / "old.txt", "Time-stamped")
        _age_file(f, 60)
        original_mtime = f.stat().st_mtime
        dest = cold_storage / "old.txt"
        success, _, _ = move_with_rollback(f, dest, OperationType.MOVE)
        assert success is True
        assert abs(dest.stat().st_mtime - original_mtime) < 2  # within 2 s

    def test_copy_preserves_mtime(self, hot_storage, cold_storage):
        """Copied file retains its original mtime."""
        from app.services.file_mover import move_with_rollback

        f = make_binary_file(hot_storage / "data.bin", 512)
        _age_file(f, 30)
        original_mtime = f.stat().st_mtime
        dest = cold_storage / "data.bin"
        success, _, _ = move_with_rollback(f, dest, OperationType.COPY)
        assert success is True
        assert abs(dest.stat().st_mtime - original_mtime) < 2

    # ── Thaw operations (reverse MOVE via FileMover) ──────────────────────────
    #
    # _thaw_single_file creates its own SessionFactory, which points to
    # the real :memory: engine (not the pytest test DB).  We instead
    # test the identical underlying file-level operation directly:
    # a reverse MOVE from cold back to hot.

    def test_thaw_text_file_moves_back(self, hot_storage, cold_storage):
        """Reverse MOVE: text file restored from cold to hot."""
        from app.services.file_mover import move_with_rollback

        cold_file = make_text_file(cold_storage / "archived.txt", "Archived content")
        hot_dest = hot_storage / "archived.txt"
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert not cold_file.exists()
        assert hot_dest.read_text() == "Archived content"

    def test_thaw_binary_file(self, hot_storage, cold_storage):
        """Reverse MOVE: binary file restored byte-for-byte."""
        from app.services.file_mover import move_with_rollback

        cold_file = make_binary_file(cold_storage / "data.bin", 4096)
        original = cold_file.read_bytes()
        hot_dest = hot_storage / "data.bin"
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert hot_dest.read_bytes() == original

    def test_thaw_png_file(self, hot_storage, cold_storage):
        """Reverse MOVE: PNG image restored byte-for-byte."""
        from app.services.file_mover import move_with_rollback

        cold_file = make_png_file(cold_storage / "image.png")
        original = cold_file.read_bytes()
        hot_dest = hot_storage / "image.png"
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert hot_dest.read_bytes() == original

    def test_thaw_removes_existing_symlink_then_restores(self, hot_storage, cold_storage):
        """
        When a symlink exists at hot_dest (pointing to cold file),
        the thaw logic removes it and moves the actual file back.
        We replicate this 2-step behaviour directly.
        """
        from app.services.file_mover import move_with_rollback

        cold_file = make_text_file(cold_storage / "file.txt", "Thawed content")
        hot_dest = hot_storage / "file.txt"
        hot_dest.symlink_to(cold_file)  # simulate existing symlink

        # Step 1: remove symlink (as _thaw_single_file does)
        if hot_dest.is_symlink():
            hot_dest.unlink()

        # Step 2: move cold file back to hot
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert not hot_dest.is_symlink()
        assert hot_dest.read_text() == "Thawed content"

    def test_thaw_zip_file(self, hot_storage, cold_storage):
        """Reverse MOVE: ZIP archive restored intact."""
        from app.services.file_mover import move_with_rollback

        cold_file = make_zip_file(cold_storage / "archive.zip")
        original = cold_file.read_bytes()
        hot_dest = hot_storage / "archive.zip"
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert hot_dest.read_bytes() == original

    def test_thaw_large_file(self, hot_storage, cold_storage):
        """Reverse MOVE: 2 MiB file restored with correct size."""
        from app.services.file_mover import move_with_rollback

        cold_file = make_large_file(cold_storage / "big.bin", 2)
        hot_dest = hot_storage / "big.bin"
        success, _, _ = move_with_rollback(cold_file, hot_dest, OperationType.MOVE)
        assert success is True
        assert hot_dest.stat().st_size == 2 * 1024 * 1024


# ==============================================================================
# Part 6 – Criteria semantics: which files get moved, which stay
# ==============================================================================


class TestCriteriaSemanticsWithHotColdStorage:
    """
    Verify the HOT-vs-COLD decision logic using CriteriaMatcher directly,
    simulating what the scan loop does:
        is_active=True  → file stays in HOT storage
        is_active=False → file moves to COLD storage
    """

    def _check(self, file_path: Path, criteria: list) -> bool:
        """Return True if file stays hot, False if it should go cold."""
        is_active, _ = CriteriaMatcher.match_file(file_path, criteria)
        return is_active

    # ── By modification time ──────────────────────────────────────────────────

    def test_old_log_goes_cold(self, hot_storage, make_criterion):
        """Log aged 2 days should go to cold (does not match 'mtime < 60')."""
        f = make_log_file(hot_storage / "old_app.log")
        _age_file(f, 60 * 24 * 2)
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        assert self._check(f, [c]) is False

    def test_fresh_text_stays_hot(self, hot_storage, make_criterion):
        """Freshly created text file stays hot (matches 'mtime < 60')."""
        f = make_text_file(hot_storage / "fresh.txt")
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        assert self._check(f, [c]) is True

    def test_stale_json_goes_cold(self, hot_storage, make_criterion):
        """JSON config 2 h old goes to cold (does not match 'mtime < 60')."""
        f = make_json_file(hot_storage / "legacy.json")
        _age_file(f, 120)
        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")
        assert self._check(f, [c]) is False

    # ── By file size ──────────────────────────────────────────────────────────

    def test_large_binary_goes_cold(self, hot_storage, make_criterion):
        """2 MiB binary goes cold when 'keep if size < 1M'."""
        f = make_large_file(hot_storage / "big.bin", 2)
        c = make_criterion(CriterionType.SIZE, Operator.LT, "1M")
        assert self._check(f, [c]) is False

    def test_small_txt_stays_hot(self, hot_storage, make_criterion):
        """Small text file stays hot when 'keep if size < 1M'."""
        f = make_text_file(hot_storage / "small.txt", "Hi")
        c = make_criterion(CriterionType.SIZE, Operator.LT, "1M")
        assert self._check(f, [c]) is True

    # ── By file name ──────────────────────────────────────────────────────────

    def test_log_stays_hot_by_name_glob(self, hot_storage, make_criterion):
        """Log file stays hot when 'keep if name matches *.log'."""
        f = make_log_file(hot_storage / "events.log")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.log")
        assert self._check(f, [c]) is True

    def test_txt_goes_cold_when_only_logs_kept(self, hot_storage, make_criterion):
        """Text file goes cold when only *.log files are kept hot."""
        f = make_text_file(hot_storage / "readme.txt")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.log")
        assert self._check(f, [c]) is False

    def test_pdf_goes_cold_by_name_regex(self, hot_storage, make_criterion):
        """PDF file goes cold: 'keep if name matches *.txt' excludes PDFs."""
        f = make_pdf_file(hot_storage / "contract.pdf")
        c = make_criterion(CriterionType.NAME, Operator.MATCHES, "*.txt")
        assert self._check(f, [c]) is False

    # ── Combined criteria ─────────────────────────────────────────────────────

    def test_recent_large_log_stays_hot(self, hot_storage, make_criterion):
        """Recent large log: passes all three criteria → stays hot."""
        f = make_large_file(hot_storage / "system.log", 2)
        # Rename with .log suffix (make_large_file doesn't set name)
        f = f.rename(hot_storage / "system.log")
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.LT, "60"),   # fresh
            make_criterion(CriterionType.SIZE, Operator.GT, "1M"),    # large
            make_criterion(CriterionType.NAME, Operator.MATCHES, "*.log"),
        ]
        assert self._check(f, criteria) is True

    def test_old_small_csv_goes_cold(self, hot_storage, make_criterion):
        """Old small CSV fails MTIME criterion → goes cold."""
        f = make_csv_file(hot_storage / "report.csv")
        _age_file(f, 120)
        criteria = [
            make_criterion(CriterionType.MTIME, Operator.LT, "60"),   # False → cold
            make_criterion(CriterionType.SIZE, Operator.LT, "10M"),   # True (doesn't matter)
        ]
        assert self._check(f, criteria) is False

    def test_mixed_file_suite_mtime_segregation(self, hot_storage, make_criterion):
        """
        Out of a mixed suite, only fresh files satisfy 'mtime < 60'.
        Files aged 2+ hours should go cold.
        """
        fresh_files = {
            "new_log": make_log_file(hot_storage / "new.log"),
            "new_json": make_json_file(hot_storage / "new.json"),
            "new_txt": make_text_file(hot_storage / "new.txt"),
        }
        old_files = {
            "old_csv": make_csv_file(hot_storage / "old.csv"),
            "old_pdf": make_pdf_file(hot_storage / "old.pdf"),
            "old_bin": make_binary_file(hot_storage / "old.bin", 1024),
        }
        for f in old_files.values():
            _age_file(f, 120)

        c = make_criterion(CriterionType.MTIME, Operator.LT, "60")

        for label, f in fresh_files.items():
            assert self._check(f, [c]) is True, f"{label} should stay hot"
        for label, f in old_files.items():
            assert self._check(f, [c]) is False, f"{label} should go cold"
