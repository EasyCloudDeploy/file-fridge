"""Criteria matching service - find-compatible file matching."""
import os
import re
import stat
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
        Check if file matches all enabled criteria.
        
        For symlinks pointing to cold storage, checks BOTH the symlink and the actual file.
        Matches if EITHER the symlink OR the actual file matches all criteria.
        
        Args:
            file_path: The file path to check (may be a symlink)
            criteria: List of criteria to match against
            actual_file_path: Optional path to the actual file (for symlinks pointing to cold storage)
        
        Returns:
            (matches: bool, matched_criteria_ids: List[int])
        """
        if not criteria:
            logger.debug(f"File {file_path}: No criteria defined, NOT matching (files should be moved back from cold storage)")
            return False, []
        
        enabled_criteria = [c for c in criteria if c.enabled]
        if not enabled_criteria:
            logger.debug(f"File {file_path}: No enabled criteria, NOT matching (files should be moved back from cold storage)")
            return False, []
        
        # If this is a symlink pointing to cold storage, check both the symlink and the actual file
        if file_path.is_symlink() and actual_file_path:
            logger.debug(f"File {file_path}: Is symlink pointing to cold storage, will check BOTH symlink and actual file")
            
            # Check the symlink itself
            try:
                symlink_stat = file_path.lstat()  # lstat() doesn't follow symlinks
                logger.debug(f"File {file_path}: Checking symlink metadata")
                symlink_matches, symlink_matched_ids = CriteriaMatcher._check_criteria(
                    file_path, symlink_stat, enabled_criteria, "symlink"
                )
            except (OSError, FileNotFoundError) as e:
                logger.debug(f"File {file_path}: Cannot lstat symlink - {e}")
                symlink_matches = False
                symlink_matched_ids = []
            
            # Check the actual file in cold storage
            try:
                actual_stat = actual_file_path.stat()  # stat() follows symlinks to get actual file
                logger.debug(f"File {file_path}: Checking actual file at {actual_file_path}")
                actual_matches, actual_matched_ids = CriteriaMatcher._check_criteria(
                    actual_file_path, actual_stat, enabled_criteria, "actual file"
                )
            except (OSError, FileNotFoundError) as e:
                logger.debug(f"File {file_path}: Cannot stat actual file {actual_file_path} - {e}")
                actual_matches = False
                actual_matched_ids = []
            
            # Match if EITHER symlink OR actual file matches
            if symlink_matches:
                logger.debug(f"File {file_path}: Symlink matches all criteria - FILE WILL BE MOVED")
                return True, symlink_matched_ids
            elif actual_matches:
                logger.debug(f"File {file_path}: Actual file matches all criteria - FILE WILL BE MOVED")
                return True, actual_matched_ids
            else:
                logger.debug(f"File {file_path}: Neither symlink nor actual file matches all criteria - will not be moved")
                return False, []
        
        # Regular file or symlink not pointing to cold storage - check normally
        # If actual_file_path is provided, use it for stat operations
        stat_path = actual_file_path if actual_file_path else file_path
        
        # If file_path is a symlink and no actual_file_path provided, try to resolve it
        if file_path.is_symlink() and not actual_file_path:
            try:
                stat_path = file_path.resolve(strict=True)
                logger.debug(f"File {file_path}: Resolved symlink to {stat_path}")
            except (OSError, RuntimeError):
                # If resolution fails, fall back to the symlink itself
                stat_path = file_path
                logger.debug(f"File {file_path}: Could not resolve symlink, using symlink itself")
        
        try:
            # Use lstat for symlink metadata, stat for actual file metadata
            if actual_file_path or (file_path.is_symlink() and stat_path != file_path):
                # Check the actual file, not the symlink
                stat_info = stat_path.stat()
                logger.debug(f"File {file_path}: Checking actual file at {stat_path} (symlink target)")
            else:
                # Regular file, use normal stat
                stat_info = file_path.stat()
                logger.debug(f"File {file_path}: Checking regular file")
        except (OSError, FileNotFoundError) as e:
            logger.debug(f"File {file_path}: Cannot stat file - {e}")
            return False, []
        
        # Check criteria for regular file
        matches, matched_ids = CriteriaMatcher._check_criteria(file_path, stat_info, enabled_criteria, "file")
        if matches:
            logger.debug(f"File {file_path}: All {len(matched_ids)} criteria matched - FILE WILL BE MOVED")
        return matches, matched_ids
    
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
                    # Use the most recent of atime or Last Open
                    if last_open_time > atime:
                        atime = last_open_time
                        used_source = "macOS Last Open"
                        logger.debug(f"File {file_path}: Using macOS Last Open time ({datetime.fromtimestamp(last_open_time)}) instead of atime ({datetime.fromtimestamp(original_atime)})")
                    else:
                        used_source = "atime (newer than Last Open)"
                        logger.debug(f"File {file_path}: Using atime ({datetime.fromtimestamp(original_atime)}) instead of macOS Last Open ({datetime.fromtimestamp(last_open_time)})")
                else:
                    logger.debug(f"File {file_path}: macOS Last Open time not available, using atime ({datetime.fromtimestamp(original_atime)})")
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
        """Match time-based criteria (mtime, atime, ctime). Value is in minutes."""
        try:
            minutes = float(value)
            # Convert to minutes since epoch
            file_minutes = timestamp / 60.0
            now_minutes = datetime.now().timestamp() / 60.0
            age_minutes = now_minutes - file_minutes
            
            if operator == Operator.GT:
                return age_minutes > minutes
            elif operator == Operator.LT:
                return age_minutes < minutes
            elif operator == Operator.EQ:
                return abs(age_minutes - minutes) < 0.5  # Within half a minute
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
                                # Try parsing with timezone
                                if '+' in date_str or (date_str.count('-') >= 3 and date_str[-5] in '+-'):
                                    # Has timezone info - mdls returns UTC times
                                    parts = date_str.rsplit(' ', 1)
                                    if len(parts) == 2:
                                        date_part, tz_part = parts
                                        # Parse as UTC time
                                        dt_utc = datetime.strptime(date_part, '%Y-%m-%d %H:%M:%S')
                                        # mdls returns times in UTC, so we need to treat this as UTC
                                        # Create a timezone-aware datetime in UTC
                                        from datetime import timezone
                                        dt_utc_aware = dt_utc.replace(tzinfo=timezone.utc)
                                        # Convert to timestamp (this will be correct regardless of local timezone)
                                        timestamp = dt_utc_aware.timestamp()
                                        logger.debug(f"File {file_path}: Got Last Open from mdls (UTC): {date_str} -> {timestamp} ({datetime.fromtimestamp(timestamp)})")
                                        return timestamp
                                else:
                                    # No timezone, assume UTC (mdls typically returns UTC)
                                    from datetime import timezone
                                    dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                                    dt_utc = dt.replace(tzinfo=timezone.utc)
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

