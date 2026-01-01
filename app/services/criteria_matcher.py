"""Criteria matching service - find-compatible file matching."""
import os
import re
import stat
import time
import fnmatch
import logging
import platform
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.models import CriterionType, Operator, Criteria

logger = logging.getLogger(__name__)


class CriteriaMatcher:
    """Matches files against criteria (find-compatible)."""
    
    @staticmethod
    def match_file(file_path: Path, criteria: List[Criteria], actual_file_path: Optional[Path] = None) -> tuple[bool, List[int]]:
        """
        Evaluates if a file matches the criteria (is ACTIVE and should be kept in HOT storage).

        Criteria define what files should be KEPT in hot storage, not what to move to cold.
        Example: "atime < 3" means "keep files accessed in last 3 minutes in hot storage"

        Returns:
            (True, IDs) if ALL criteria match - file is ACTIVE and should be in HOT storage
            (False, []) if ANY criterion doesn't match - file is INACTIVE and should be in COLD storage
        """
        if not criteria:
            # No criteria means no files are considered active, move all to cold
            return False, []

        enabled_criteria = [c for c in criteria if c.enabled]
        if not enabled_criteria:
            # No enabled criteria means no files are considered active, move all to cold
            return False, []

        # Target the actual file metadata (ignore symlink itself)
        stat_path = actual_file_path if actual_file_path else file_path

        try:
            # We follow symlinks to get the actual target's metadata
            stat_info = stat_path.stat()

            # Simple, direct criteria evaluation
            return CriteriaMatcher._check_criteria(
                file_path, stat_info, enabled_criteria, "file"
            )
        except (OSError, FileNotFoundError) as e:
            logger.debug(f"File {file_path}: Cannot stat - {e}")
            return False, []

    @staticmethod
    def _check_criteria(file_path: Path, stat_info: os.stat_result, criteria: List[Criteria], context: str = "file") -> tuple[bool, List[int]]:
        """
        Check if a file (or symlink) matches all criteria.

        Args:
            file_path: The file path (for logging and name-based criteria)
            stat_info: The stat result to check
            criteria: List of enabled criteria to match
            context: Context string for logging (e.g., "symlink", "actual file", "file")

        Returns:
            (matches: bool, matched_criteria_ids: List[int])
        """
        matched_ids = []
        logger.debug(f"File {file_path}: Evaluating {len(criteria)} enabled criteria ({context})")

        for criterion in criteria:
            matches = CriteriaMatcher._match_criterion(file_path, stat_info, criterion)
            if matches:
                logger.debug(f"File {file_path}: ✓ Criterion {criterion.id} ({criterion.criterion_type.value} {criterion.operator.value} {criterion.value}) MATCHED ({context})")
                matched_ids.append(criterion.id)
            else:
                logger.debug(f"File {file_path}: ✗ Criterion {criterion.id} ({criterion.criterion_type.value} {criterion.operator.value} {criterion.value}) NOT MATCHED ({context})")
                return False, []

        logger.debug(f"File {file_path}: All {len(criteria)} criteria matched ({context})")
        return True, matched_ids
    
    @staticmethod
    def _match_criterion(file_path: Path, stat_info: os.stat_result, criterion: Criteria) -> bool:
        """Match a single criterion."""
        criterion_type = criterion.criterion_type
        operator = criterion.operator
        value = criterion.value

        if criterion_type == CriterionType.MTIME:
            return CriteriaMatcher._match_time(
                stat_info.st_mtime, operator, value, "mtime"
            )
        elif criterion_type == CriterionType.ATIME:
            # On macOS, also check "Last Open" metadata from extended attributes
            # Use the most recent of atime or Last Open date
            atime = stat_info.st_atime
            original_atime = atime
            used_source = "atime"
            if platform.system() == "Darwin":  # macOS
                last_open_time = CriteriaMatcher._get_macos_last_open_time(file_path)
                if last_open_time is not None:
                    # Last Open time exists - use the most recent of atime or Last Open
                    if last_open_time > atime:
                        atime = last_open_time
                        used_source = "macOS Last Open"
                        logger.debug(f"File {file_path}: Using macOS Last Open time ({datetime.fromtimestamp(last_open_time)}) instead of atime ({datetime.fromtimestamp(original_atime)})")
                    else:
                        used_source = "atime (newer than Last Open)"
                        logger.debug(f"File {file_path}: Using atime ({datetime.fromtimestamp(original_atime)}) instead of macOS Last Open ({datetime.fromtimestamp(last_open_time)})")
                else:
                    # Last Open time is None - file has NEVER been opened by user
                    # Treat as "infinitely old" (epoch time) so it's moved to cold storage
                    # Don't use atime as fallback because atime can be recent even if file was never opened
                    atime = 0.0  # Unix epoch (Jan 1, 1970)
                    used_source = "macOS Last Open (never opened - using epoch)"
                    logger.debug(f"File {file_path}: macOS Last Open time not available (never opened), treating as very old (epoch time) instead of using atime ({datetime.fromtimestamp(original_atime)})")
            else:
                logger.debug(f"File {file_path}: Non-macOS system, using atime ({datetime.fromtimestamp(original_atime)})")

            logger.debug(f"File {file_path}: Final atime for criteria check: {datetime.fromtimestamp(atime)} (source: {used_source})")
            return CriteriaMatcher._match_time(
                atime, operator, value, "atime"
            )
        elif criterion_type == CriterionType.CTIME:
            return CriteriaMatcher._match_time(
                stat_info.st_ctime, operator, value, "ctime"
            )
        elif criterion_type == CriterionType.SIZE:
            return CriteriaMatcher._match_size(stat_info.st_size, operator, value)
        elif criterion_type == CriterionType.NAME:
            return CriteriaMatcher._match_name(file_path.name, operator, value, case_sensitive=True)
        elif criterion_type == CriterionType.INAME:
            return CriteriaMatcher._match_name(file_path.name, operator, value, case_sensitive=False)
        elif criterion_type == CriterionType.TYPE:
            return CriteriaMatcher._match_type(file_path, stat_info, value)
        elif criterion_type == CriterionType.PERM:
            return CriteriaMatcher._match_perm(stat_info.st_mode, value)
        elif criterion_type == CriterionType.USER:
            return CriteriaMatcher._match_user(stat_info.st_uid, value)
        elif criterion_type == CriterionType.GROUP:
            return CriteriaMatcher._match_group(stat_info.st_gid, value)
        else:
            return False
    
    @staticmethod
    def _match_time(timestamp: float, operator: Operator, value: str, time_type: str) -> bool:
        """
        Match time-based criteria (mtime, atime, ctime). Value is in minutes.

        All comparisons use Unix timestamps (seconds since epoch) which are timezone-agnostic.
        Direct evaluation without hysteresis - timestamps are naturally stable and files only
        age in one direction unless actually accessed by users.
        """
        try:
            minutes = float(value)
            # Use time.time() for current Unix timestamp
            current_time = time.time()
            age_seconds = current_time - timestamp
            age_minutes = age_seconds / 60.0

            # Simple, direct comparisons
            if operator == Operator.GT:
                return age_minutes > minutes
            elif operator == Operator.LT:
                return age_minutes < minutes
            elif operator == Operator.EQ:
                # Small tolerance for exact time matching (0.5 minutes = 30 seconds)
                return abs(age_minutes - minutes) < 0.5
            elif operator == Operator.GTE:
                return age_minutes >= minutes
            elif operator == Operator.LTE:
                return age_minutes <= minutes
        except (ValueError, TypeError):
            return False
        return False
    
    @staticmethod
    def _match_size(size: int, operator: Operator, value: str) -> bool:
        """Match file size criteria."""
        try:
            # Parse size value (supports suffixes: c, k, M, G)
            value_lower = value.lower().strip()
            multiplier = 1
            
            if value_lower.endswith('c'):
                multiplier = 1
                value_lower = value_lower[:-1]
            elif value_lower.endswith('k'):
                multiplier = 1024
                value_lower = value_lower[:-1]
            elif value_lower.endswith('m'):
                multiplier = 1024 * 1024
                value_lower = value_lower[:-1]
            elif value_lower.endswith('g'):
                multiplier = 1024 * 1024 * 1024
                value_lower = value_lower[:-1]
            
            target_size = int(float(value_lower) * multiplier)
            
            if operator == Operator.GT:
                return size > target_size
            elif operator == Operator.LT:
                return size < target_size
            elif operator == Operator.EQ:
                return size == target_size
            elif operator == Operator.GTE:
                return size >= target_size
            elif operator == Operator.LTE:
                return size <= target_size
        except (ValueError, TypeError):
            return False
        return False
    
    @staticmethod
    def _match_name(filename: str, operator: Operator, value: str, case_sensitive: bool = True) -> bool:
        """Match filename criteria."""
        if not case_sensitive:
            filename = filename.lower()
            value = value.lower()
        
        if operator == Operator.EQ:
            return filename == value
        elif operator == Operator.CONTAINS:
            return value in filename
        elif operator == Operator.MATCHES:
            return fnmatch.fnmatch(filename, value)
        elif operator == Operator.REGEX:
            try:
                return bool(re.search(value, filename))
            except re.error:
                return False
        return False
    
    @staticmethod
    def _match_type(file_path: Path, stat_info: os.stat_result, value: str) -> bool:
        """Match file type criteria."""
        if value == 'f' or value == 'file':
            return file_path.is_file()
        elif value == 'd' or value == 'directory':
            return file_path.is_dir()
        elif value == 'l' or value == 'link':
            return file_path.is_symlink()
        return False
    
    @staticmethod
    def _match_perm(mode: int, value: str) -> bool:
        """Match permission criteria."""
        try:
            # Support octal (e.g., "755") or symbolic (e.g., "u=rwx")
            if value.isdigit():
                target_perm = int(value, 8)
                return (mode & 0o777) == target_perm
            else:
                # Simple symbolic permission matching
                # For now, just check if file is readable/writable/executable
                if 'r' in value and not (mode & stat.S_IRUSR):
                    return False
                if 'w' in value and not (mode & stat.S_IWUSR):
                    return False
                if 'x' in value and not (mode & stat.S_IXUSR):
                    return False
                return True
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def _match_user(uid: int, value: str) -> bool:
        """Match user criteria."""
        try:
            import pwd
            target_uid = pwd.getpwnam(value).pw_uid
            return uid == target_uid
        except (KeyError, ValueError, ImportError):
            try:
                target_uid = int(value)
                return uid == target_uid
            except (ValueError, TypeError):
                return False
    
    @staticmethod
    def _match_group(gid: int, value: str) -> bool:
        """Match group criteria."""
        try:
            import grp
            target_gid = grp.getgrnam(value).gr_gid
            return gid == target_gid
        except (KeyError, ValueError, ImportError):
            try:
                target_gid = int(value)
                return gid == target_gid
            except (ValueError, TypeError):
                return False
    
    @staticmethod
    def _get_macos_last_open_time(file_path: Path) -> Optional[float]:
        """
        Get macOS "Last Open" time from extended attributes or Spotlight metadata.
        
        On macOS, Finder access updates extended attributes rather than atime.
        This function attempts to retrieve the Last Open date using:
        1. mdls (Spotlight metadata) - kMDItemLastUsedDate
        2. xattr extended attributes - com.apple.lastuseddate#PS
        
        Returns:
            Unix timestamp as float, or None if not available
        """
        if platform.system() != "Darwin":
            return None
        
        try:
            # Method 1: Try mdls (Spotlight metadata) - most reliable
            try:
                result = subprocess.run(
                    ['mdls', '-name', 'kMDItemLastUsedDate', str(file_path)],
                    capture_output=True,
                    timeout=2,
                    text=True
                )
                if result.returncode == 0 and result.stdout:
                    # Parse output like: kMDItemLastUsedDate = 2024-01-01 12:00:00 +0000
                    output = result.stdout.strip()
                    if '=' in output:
                        date_str = output.split('=', 1)[1].strip()
                        if date_str and date_str != '(null)':
                            # Parse date string (format: YYYY-MM-DD HH:MM:SS +0000 or YYYY-MM-DD HH:MM:SS -0000)
                            try:
                                from datetime import timezone
                                # Try parsing with timezone first
                                if '+' in date_str or (date_str.count('-') >= 3 and date_str[-5] in '+-'):
                                    # Has timezone info - use strptime with %z to properly parse timezone
                                    try:
                                        # Parse with timezone (e.g., "2024-01-15 10:30:00 +0000")
                                        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S %z')
                                        # dt is now timezone-aware, convert to Unix timestamp
                                        # timestamp() will correctly convert from any timezone to Unix time
                                        timestamp = dt.timestamp()
                                        logger.debug(f"File {file_path}: Got Last Open from mdls: {date_str} -> {timestamp} ({datetime.fromtimestamp(timestamp)})")
                                        return timestamp
                                    except ValueError:
                                        # If %z parsing fails, try manual UTC parsing
                                        parts = date_str.rsplit(' ', 1)
                                        if len(parts) == 2:
                                            date_part = parts[0]
                                            dt_naive = datetime.strptime(date_part, '%Y-%m-%d %H:%M:%S')
                                            # Assume UTC and create timezone-aware datetime
                                            dt_utc = dt_naive.replace(tzinfo=timezone.utc)
                                            timestamp = dt_utc.timestamp()
                                            logger.debug(f"File {file_path}: Got Last Open from mdls (manual UTC): {date_str} -> {timestamp} ({datetime.fromtimestamp(timestamp)})")
                                            return timestamp
                                else:
                                    # No timezone info, assume UTC (mdls typically returns UTC)
                                    dt_naive = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                                    # Mark as UTC timezone-aware
                                    dt_utc = dt_naive.replace(tzinfo=timezone.utc)
                                    timestamp = dt_utc.timestamp()
                                    logger.debug(f"File {file_path}: Got Last Open from mdls (assumed UTC): {date_str} -> {timestamp} ({datetime.fromtimestamp(timestamp)})")
                                    return timestamp
                            except ValueError as e:
                                logger.debug(f"File {file_path}: Failed to parse mdls date '{date_str}': {e}")
                                pass
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            
            # Method 2: Try xattr extended attributes
            try:
                result = subprocess.run(
                    ['xattr', '-p', 'com.apple.lastuseddate#PS', str(file_path)],
                    capture_output=True,
                    timeout=2,
                    text=True
                )
                if result.returncode == 0 and result.stdout:
                    # Extended attribute format may vary, try to parse
                    # This is a fallback if mdls doesn't work
                    pass
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            
        except Exception as e:
            logger.debug(f"File {file_path}: Error getting macOS Last Open time: {e}")
        
        return None

