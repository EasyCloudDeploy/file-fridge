"""Tests for checksum verification service."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services.checksum_verifier import ChecksumVerifier, checksum_verifier


@pytest.mark.unit
class TestChecksumVerifier:
    def test_calculate_checksum_sha256(self, tmp_path: Path) -> None:
        file_path = tmp_path / "data.bin"
        content = b"hello checksum world"
        file_path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        assert ChecksumVerifier.calculate_checksum(file_path) == expected

    def test_calculate_checksum_md5(self, tmp_path: Path) -> None:
        file_path = tmp_path / "data.bin"
        content = b"md5 payload"
        file_path.write_bytes(content)
        expected = hashlib.md5(content).hexdigest()

        assert ChecksumVerifier.calculate_checksum(file_path, algorithm="md5") == expected

    def test_calculate_checksum_os_error_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_path = tmp_path / "missing.bin"

        def fail_open(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "open", fail_open)
        assert ChecksumVerifier.calculate_checksum(file_path) is None

    def test_calculate_checksum_unexpected_error_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_path = tmp_path / "data.bin"
        file_path.write_bytes(b"x")

        monkeypatch.setattr(
            hashlib,
            "new",
            MagicMock(side_effect=ValueError("bad algorithm")),
        )
        assert ChecksumVerifier.calculate_checksum(file_path) is None

    def test_verify_checksum_match(self, tmp_path: Path) -> None:
        file_path = tmp_path / "verify.bin"
        content = b"verify me"
        file_path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()

        assert ChecksumVerifier.verify_checksum(file_path, digest) is True
        assert ChecksumVerifier.verify_checksum(file_path, digest.upper()) is True

    def test_verify_checksum_mismatch(self, tmp_path: Path) -> None:
        file_path = tmp_path / "verify.bin"
        file_path.write_bytes(b"content")

        assert ChecksumVerifier.verify_checksum(file_path, "0" * 64) is False

    def test_verify_checksum_calculation_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ChecksumVerifier,
            "calculate_checksum",
            staticmethod(lambda *_a, **_k: None),
        )
        assert ChecksumVerifier.verify_checksum(Path("/nope"), "abc") is False

    def test_verify_file_integrity_success(self, tmp_path: Path) -> None:
        content = b"same bytes"
        source = tmp_path / "src.bin"
        dest = tmp_path / "dst.bin"
        source.write_bytes(content)
        dest.write_bytes(content)

        assert ChecksumVerifier.verify_file_integrity(source, dest) is True

    def test_verify_file_integrity_source_failure(self, tmp_path: Path) -> None:
        source = tmp_path / "missing.bin"
        dest = tmp_path / "dst.bin"
        dest.write_bytes(b"x")

        assert ChecksumVerifier.verify_file_integrity(source, dest) is False

    def test_verify_file_integrity_dest_failure(self, tmp_path: Path) -> None:
        source = tmp_path / "src.bin"
        source.write_bytes(b"x")
        dest = tmp_path / "missing.bin"

        assert ChecksumVerifier.verify_file_integrity(source, dest) is False

    def test_verify_file_integrity_mismatch(self, tmp_path: Path) -> None:
        source = tmp_path / "src.bin"
        dest = tmp_path / "dst.bin"
        source.write_bytes(b"a")
        dest.write_bytes(b"b")

        assert ChecksumVerifier.verify_file_integrity(source, dest) is False

    def test_calculate_checksum_for_file_inventory_under_limit(self, tmp_path: Path) -> None:
        file_path = tmp_path / "small.bin"
        content = b"tiny"
        file_path.write_bytes(content)
        monkeypatch_limit = settings.max_file_size_mb

        try:
            settings.max_file_size_mb = 10
            result = ChecksumVerifier.calculate_checksum_for_file_inventory(
                file_path, file_size=len(content)
            )
            assert result == hashlib.sha256(content).hexdigest()
        finally:
            settings.max_file_size_mb = monkeypatch_limit

    def test_calculate_checksum_for_file_inventory_over_limit(self, tmp_path: Path) -> None:
        file_path = tmp_path / "large.bin"
        file_path.write_bytes(b"x")
        original = settings.max_file_size_mb

        try:
            settings.max_file_size_mb = 0
            assert (
                ChecksumVerifier.calculate_checksum_for_file_inventory(file_path, file_size=1024)
                is None
            )
        finally:
            settings.max_file_size_mb = original

    def test_calculate_checksum_batch(self, tmp_path: Path) -> None:
        paths = []
        expected = {}
        for idx in range(3):
            p = tmp_path / f"f{idx}.bin"
            p.write_bytes(f"file-{idx}".encode())
            paths.append(p)
            expected[p] = hashlib.sha256(p.read_bytes()).hexdigest()

        results = ChecksumVerifier.calculate_checksum_batch(paths, max_workers=2)
        assert results == expected

    def test_calculate_checksum_batch_handles_worker_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = tmp_path / "good.bin"
        good.write_bytes(b"ok")
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"bad")

        original = ChecksumVerifier.calculate_checksum

        def flaky(path: Path, algorithm: str = "sha256"):
            if path == bad:
                raise RuntimeError("boom")
            return original(path, algorithm)

        monkeypatch.setattr(ChecksumVerifier, "calculate_checksum", staticmethod(flaky))
        results = ChecksumVerifier.calculate_checksum_batch([good, bad], max_workers=1)
        assert results[good] == hashlib.sha256(b"ok").hexdigest()
        assert results[bad] is None

    def test_singleton_instance(self) -> None:
        assert isinstance(checksum_verifier, ChecksumVerifier)
