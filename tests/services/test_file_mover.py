import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from app.models import MonitoredPath, OperationType
from app.services.file_mover import (
    move_file,
    _move,
    _copy,
    _move_and_symlink,
    move_with_rollback,
    preserve_directory_structure,
)


@pytest.fixture
def source_and_dest(tmp_path):
    """Fixture to create a source file and a destination directory."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_file = source_dir / "test.txt"
    source_file.write_text("Hello, world!")

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    destination_file = dest_dir / "test.txt"

    return source_file, destination_file


# ==================================
# move_file tests
# ==================================


@patch("app.services.file_mover.shutil.disk_usage")
@patch("app.services.file_mover._move", return_value=(True, None))
def test_move_file_operation_move(mock_internal_move, mock_disk_usage, source_and_dest):
    """Test that move_file calls the internal _move function for MOVE operation."""
    source, dest = source_and_dest
    # Need enough space for file size + 1MB buffer
    mock_disk_usage.return_value = (0, 0, source.stat().st_size + 2 * 1024 * 1024)

    success, error = move_file(source, dest, OperationType.MOVE)

    assert success is True
    assert error is None
    mock_internal_move.assert_called_once_with(source, dest, None)


@patch("app.services.file_mover.shutil.disk_usage")
@patch("app.services.file_mover._copy", return_value=(True, None))
def test_move_file_operation_copy(mock_internal_copy, mock_disk_usage, source_and_dest):
    """Test that move_file calls the internal _copy function for COPY operation."""
    source, dest = source_and_dest
    # Need enough space for file size + 1MB buffer
    mock_disk_usage.return_value = (0, 0, source.stat().st_size + 2 * 1024 * 1024)

    success, error = move_file(source, dest, OperationType.COPY)

    assert success is True
    assert error is None
    mock_internal_copy.assert_called_once_with(source, dest, None)


@patch("app.services.file_mover.shutil.disk_usage")
@patch("app.services.file_mover._move_and_symlink", return_value=(True, None))
def test_move_file_operation_symlink(mock_internal_symlink, mock_disk_usage, source_and_dest):
    """Test that move_file calls the internal _move_and_symlink function for SYMLINK operation."""
    source, dest = source_and_dest
    # Need enough space for file size + 1MB buffer
    mock_disk_usage.return_value = (0, 0, source.stat().st_size + 2 * 1024 * 1024)

    success, error = move_file(source, dest, OperationType.SYMLINK)

    assert success is True
    assert error is None
    mock_internal_symlink.assert_called_once_with(source, dest, None)


@patch("app.services.file_mover.shutil.disk_usage")
def test_move_file_not_enough_space(mock_disk_usage, source_and_dest):
    """Test that move_file fails if there is not enough disk space."""
    source, dest = source_and_dest
    mock_disk_usage.return_value = (0, 0, 1)  # Not enough space

    success, error = move_file(source, dest, OperationType.MOVE)

    assert success is False
    assert "Not enough space" in error


def test_move_file_source_not_exists(tmp_path):
    """Test move_file when source file does not exist."""
    source = tmp_path / "nonexistent.txt"
    dest = tmp_path / "dest" / "nonexistent.txt"

    success, error = move_file(source, dest, OperationType.MOVE)

    assert success is False
    assert "Source file no longer exists" in error


# ==================================
# _move tests
# ==================================


@patch("pathlib.Path.rename")
def test_move_same_filesystem(mock_rename, source_and_dest):
    """Test _move on the same filesystem uses rename."""
    source, dest = source_and_dest
    mock_rename.return_value = None  # Simulate successful rename

    success, error = _move(source, dest)

    assert success is True
    assert error is None
    mock_rename.assert_called_once_with(dest)


@patch("pathlib.Path.rename", side_effect=OSError)
@patch("app.services.file_mover._copy_with_progress")
@patch("pathlib.Path.unlink")
def test_move_cross_filesystem(mock_unlink, mock_copy, mock_rename, source_and_dest):
    """Test _move across filesystems uses copy and unlink."""
    source, dest = source_and_dest

    success, error = _move(source, dest)

    assert success is True
    assert error is None
    mock_rename.assert_called_once_with(dest)
    mock_copy.assert_called_once_with(source, dest, None)
    mock_unlink.assert_called_once()


# ==================================
# _copy tests
# ==================================


@patch("app.services.file_mover._copy_with_progress")
def test_copy(mock_copy_with_progress, source_and_dest):
    """Test that _copy calls _copy_with_progress."""
    source, dest = source_and_dest
    success, error = _copy(source, dest, None)

    assert success is True
    assert error is None
    mock_copy_with_progress.assert_called_once_with(source, dest, None)


@patch("shutil.copy2", side_effect=Exception("Disk full"))
def test_copy_exception(mock_copy2, source_and_dest):
    """Test that _copy handles exceptions."""
    source, dest = source_and_dest
    success, error = _copy(source, dest, None)

    assert success is False
    assert "Copy failed: Disk full" in error


# ==================================
# preserve_directory_structure tests
# ==================================


def test_preserve_directory_structure():
    """Test preserve_directory_structure function."""
    base_source = Path("/data/hot")
    base_dest = Path("/data/cold")
    source_path = Path("/data/hot/movies/2023/movie.mkv")

    result = preserve_directory_structure(source_path, base_source, base_dest)

    assert result == Path("/data/cold/movies/2023/movie.mkv")


def test_preserve_directory_structure_no_relation():
    """Test preserve_directory_structure when source is not in base_source."""
    base_source = Path("/data/hot")
    base_dest = Path("/data/cold")
    source_path = Path("/downloads/file.zip")

    result = preserve_directory_structure(source_path, base_source, base_dest)

    assert result == Path("/data/cold/file.zip")


# ==================================
# _move_and_symlink tests
# ==================================


@patch("app.services.file_mover._move")
@patch("pathlib.Path.symlink_to")
def test_move_and_symlink_file(mock_symlink_to, mock_move, source_and_dest):
    """Test _move_and_symlink for a regular file."""
    source, dest = source_and_dest
    mock_move.return_value = (True, None)

    success, error = _move_and_symlink(source, dest)

    assert success is True
    assert error is None
    mock_move.assert_called_once_with(source, dest, None)
    mock_symlink_to.assert_called_once()


@patch("app.services.file_mover._move")
@patch("pathlib.Path.symlink_to", side_effect=OSError("Permission denied"))
def test_move_and_symlink_rollback(
    mock_symlink_to, mock_move, source_and_dest, mocker
):  # Added mocker
    """Test that _move_and_symlink rolls back the move if symlink creation fails."""
    source, dest = source_and_dest
    mock_move.return_value = (True, None)

    # Mock pathlib.Path.rename at the class level
    # Configure it to do nothing for the initial move,
    # but track calls for the rollback
    mock_path_rename = mocker.patch("pathlib.Path.rename", autospec=True)

    success, error = _move_and_symlink(source, dest)

    assert success is False
    assert "Symlink creation failed" in error
    # The rollback calls dest.rename(source)
    mock_path_rename.assert_called_once_with(dest, source)


# ==================================
# move_with_rollback tests
# ==================================


@patch("app.services.file_mover.checksum_verifier")
@patch("app.services.file_mover._move")
def test_move_with_rollback_success(mock_move, mock_verifier, source_and_dest):
    """Test move_with_rollback with successful move and checksum verification."""
    source, dest = source_and_dest
    mock_move.return_value = (True, None)
    mock_verifier.calculate_checksum.side_effect = ["checksum1", "checksum1"]

    success, error, checksum = move_with_rollback(source, dest, OperationType.MOVE)

    assert success is True
    assert error is None
    assert checksum == "checksum1"
    mock_move.assert_called_once_with(source, dest, None)
    mock_verifier.calculate_checksum.assert_has_calls([call(source), call(dest)])


@patch("app.services.file_mover.checksum_verifier")
@patch("app.services.file_mover._move")
def test_move_with_rollback_checksum_mismatch(
    mock_move, mock_verifier, source_and_dest, mocker
):  # Removed mock_unlink, added mocker
    """Test move_with_rollback with checksum mismatch triggers rollback."""
    source, dest = source_and_dest
    mock_move.return_value = (True, None)
    mock_verifier.calculate_checksum.side_effect = ["checksum1", "checksum2"]

    # Create the destination file so checksum_verifier doesn't fail immediately
    dest.touch()

    # Capture the real Path.exists BEFORE patching
    _original_path_exists = Path.exists

    # Patch Path.exists and Path.unlink at the class level
    mock_path_exists = mocker.patch("pathlib.Path.exists", autospec=True)
    mock_path_unlink = mocker.patch("pathlib.Path.unlink", autospec=True)

    # Return True for the dest path in rollback check; delegate to the real method otherwise
    def custom_exists_side_effect(path_obj):
        if path_obj == dest:
            return True
        return _original_path_exists(path_obj)

    mock_path_exists.side_effect = custom_exists_side_effect

    success, error, checksum = move_with_rollback(source, dest, OperationType.MOVE)
    assert success is False
    assert "Checksum verification failed" in error
    assert checksum == "checksum1"
    # Ensure unlink was called on the correct destination path
    mock_path_unlink.assert_called_once_with(dest)

def test_move_symlink_direct(tmp_path):
    """Test _move_symlink when it points to an absolute path."""
    target = tmp_path / "actual_target.txt"
    target.write_text("data")
    link = tmp_path / "the_link"
    link.symlink_to(target)
    dest = tmp_path / "final_dest.txt"
    
    from app.services.file_mover import _move_symlink
    success, error = _move_symlink(link, dest)
    
    assert success is True
    assert not link.exists()
    assert dest.exists()
    assert dest.read_text() == "data"

def test_move_symlink_relative(tmp_path):
    """Test _move_symlink when it points to a relative path."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    target = subdir / "target.txt"
    target.write_text("relative data")
    link = subdir / "link_rel"
    link.symlink_to("target.txt")
    dest = tmp_path / "moved_relative.txt"
    
    from app.services.file_mover import _move_symlink
    success, error = _move_symlink(link, dest)
    
    assert success is True
    assert not link.exists()
    assert dest.exists()
    assert dest.read_text() == "relative data"

@patch("app.services.file_mover.shutil.copy2")
@patch("app.services.file_mover.os.utime")
def test_copy_no_progress(mock_utime, mock_copy2, source_and_dest):
    """Test _copy_with_progress without callback (uses shutil.copy2)."""
    source, dest = source_and_dest
    from app.services.file_mover import _copy_with_progress
    _copy_with_progress(source, dest, None)
    mock_copy2.assert_called_once()
    mock_utime.assert_called_once()
