"""File scanning service."""
import os
from pathlib import Path
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from app.models import MonitoredPath, Criteria, PinnedFile
from app.services.criteria_matcher import CriteriaMatcher


class FileScanner:
    """Scans directories for files matching criteria."""
    
    @staticmethod
    def scan_path(path: MonitoredPath, db: Optional[Session] = None) -> List[Tuple[Path, List[int]]]:
        """
        Scan a monitored path for files matching criteria.
        
        Args:
            path: The monitored path to scan
            db: Database session to check for pinned files
        
        Returns:
            List of (file_path, matched_criteria_ids) tuples
        """
        matching_files = []
        source_path = Path(path.source_path)
        
        if not source_path.exists() or not source_path.is_dir():
            return matching_files
        
        # Get all criteria for this path
        criteria = path.criteria
        
        # Get list of pinned files if db is provided
        pinned_paths = set()
        if db:
            pinned = db.query(PinnedFile).filter(
                PinnedFile.path_id == path.id
            ).all()
            pinned_paths = {Path(p.file_path) for p in pinned}
        
        # Walk through directory
        for root, dirs, files in os.walk(source_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in files:
                # Skip hidden files
                if filename.startswith('.'):
                    continue
                
                file_path = Path(root) / filename
                
                # Skip pinned files
                if file_path in pinned_paths:
                    continue
                
                try:
                    matches, matched_ids = CriteriaMatcher.match_file(file_path, criteria)
                    if matches:
                        matching_files.append((file_path, matched_ids))
                except (OSError, PermissionError) as e:
                    # Skip files we can't access
                    continue
        
        return matching_files

