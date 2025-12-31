"""Criteria matching service - find-compatible file matching."""
import os
import re
import stat
import fnmatch
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timedelta
from app.models import CriterionType, Operator, Criteria


class CriteriaMatcher:
    """Matches files against criteria (find-compatible)."""
    
    @staticmethod
    def match_file(file_path: Path, criteria: List[Criteria]) -> tuple[bool, List[int]]:
        """
        Check if file matches all enabled criteria.
        
        Returns:
            (matches: bool, matched_criteria_ids: List[int])
        """
        if not criteria:
            return True, []
        
        enabled_criteria = [c for c in criteria if c.enabled]
        if not enabled_criteria:
            return True, []
        
        matched_ids = []
        
        try:
            stat_info = file_path.stat()
        except (OSError, FileNotFoundError):
            return False, []
        
        for criterion in enabled_criteria:
            if not CriteriaMatcher._match_criterion(file_path, stat_info, criterion):
                return False, []
            matched_ids.append(criterion.id)
        
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
            return CriteriaMatcher._match_time(
                stat_info.st_atime, operator, value, "atime"
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
        """Match time-based criteria (mtime, atime, ctime)."""
        try:
            days = float(value)
            # Convert to days since epoch
            file_days = timestamp / 86400.0
            now_days = datetime.now().timestamp() / 86400.0
            age_days = now_days - file_days
            
            if operator == Operator.GT:
                return age_days > days
            elif operator == Operator.LT:
                return age_days < days
            elif operator == Operator.EQ:
                return abs(age_days - days) < 0.5  # Within half a day
            elif operator == Operator.GTE:
                return age_days >= days
            elif operator == Operator.LTE:
                return age_days <= days
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

