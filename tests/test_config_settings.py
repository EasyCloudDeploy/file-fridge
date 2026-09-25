"""Tests for Settings validators in app.config."""

import logging
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import Settings, translate_path_for_symlink


@pytest.mark.unit
class TestSettingsValidators:
    def test_validate_secret_key_empty_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exit_mock = MagicMock(side_effect=SystemExit(1))
        monkeypatch.setattr(sys, "exit", exit_mock)

        with pytest.raises(SystemExit):
            Settings(secret_key="")

        exit_mock.assert_called_once_with(1)

    def test_validate_secret_key_whitespace_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "exit", MagicMock(side_effect=SystemExit(1)))
        with pytest.raises(SystemExit):
            Settings(secret_key="   ")

    def test_validate_secret_key_accepts_value(self) -> None:
        settings = Settings(secret_key="valid-secret")
        assert settings.secret_key == "valid-secret"

    def test_read_version_file_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text("1.2.3\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        settings = Settings(secret_key="key", app_version="0.0.0")
        assert settings.app_version == "1.2.3"

    def test_read_version_file_handles_read_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text("9.9.9", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        original_open = Path.open

        def selective_open(self, *args, **kwargs):
            if self.name == "VERSION":
                raise OSError("cannot read")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", selective_open)

        with caplog.at_level(logging.ERROR):
            settings = Settings(
                secret_key="key",
                app_version="0.0.0",
                encryption_key_file=str(tmp_path / "no-such-key"),
            )
        assert settings.app_version == "0.0.0"

    def test_encryption_key_file_missing_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "missing.key"
        with caplog.at_level(logging.WARNING):
            Settings(secret_key="key", encryption_key_file=str(missing))
        assert "Legacy encryption key file not found" in caplog.text

    def test_encryption_key_path_not_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_dir = tmp_path / "keydir"
        key_dir.mkdir()
        with caplog.at_level(logging.WARNING):
            Settings(secret_key="key", encryption_key_file=str(key_dir))
        assert "not a file" in caplog.text

    def test_encryption_key_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_file = tmp_path / "encryption.key"
        key_file.write_text("secret", encoding="utf-8")

        def fail_open(self, *args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "open", fail_open)

        with caplog.at_level(logging.WARNING):
            Settings(secret_key="key", encryption_key_file=str(key_file))
        assert "Cannot read encryption key file" in caplog.text

    def test_encryption_key_permissive_permissions_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_file = tmp_path / "encryption.key"
        key_file.write_text("secret", encoding="utf-8")
        key_file.chmod(0o644)

        with caplog.at_level(logging.WARNING):
            Settings(secret_key="key", encryption_key_file=str(key_file))
        assert "permissive permissions" in caplog.text

    def test_encryption_key_secure_permissions_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_file = tmp_path / "encryption.key"
        key_file.write_text("secret", encoding="utf-8")
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

        with caplog.at_level(logging.WARNING):
            Settings(secret_key="key", encryption_key_file=str(key_file))
        assert "permissive permissions" not in caplog.text


@pytest.mark.unit
class TestTranslatePathForSymlink:
    def test_no_prefix_returns_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "container_path_prefix", None)
        monkeypatch.setattr(config.settings, "host_path_prefix", None)
        assert translate_path_for_symlink("/data/file.txt") == "/data/file.txt"

    def test_translates_container_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "container_path_prefix", "/data")
        monkeypatch.setattr(config.settings, "host_path_prefix", "/mnt/data")

        result = translate_path_for_symlink("/data/cold/file.txt")
        assert result == "/mnt/data/cold/file.txt"

    def test_unmatched_prefix_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "container_path_prefix", "/data")
        monkeypatch.setattr(config.settings, "host_path_prefix", "/mnt/data")

        assert translate_path_for_symlink("/other/file.txt") == "/other/file.txt"
