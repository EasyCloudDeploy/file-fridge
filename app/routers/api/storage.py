"""API routes for storage management."""
import logging
import os
import shutil
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import MonitoredPath
from app.schemas import StorageStats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


@router.get("/stats", response_model=List[StorageStats])
def get_storage_stats(db: Session = Depends(get_db)):
    """Get storage statistics for all unique cold storage paths."""
    paths = db.query(MonitoredPath.cold_storage_path).distinct().all()
    
    unique_volumes = {}
    for (path_str,) in paths:
        try:
            # Get the device ID for the path
            device_id = os.stat(path_str).st_dev
            if device_id not in unique_volumes:
                unique_volumes[device_id] = path_str
        except FileNotFoundError:
            # Handle cases where the path doesn't exist
            if 'not_found' not in unique_volumes:
                unique_volumes['not_found'] = []
            unique_volumes['not_found'].append(path_str)
        except Exception as e:
            # Handle other potential errors
            logger.error(f"Error stating path {path_str}: {e}")
            if 'error' not in unique_volumes:
                unique_volumes['error'] = []
            unique_volumes['error'].append(path_str)

    stats_list = []
    for device_id, path_str in unique_volumes.items():
        if device_id == 'not_found' or device_id == 'error':
            for p in path_str:
                stats_list.append(StorageStats(
                    path=p,
                    total_bytes=0,
                    used_bytes=0,
                    free_bytes=0,
                    error=f"Path not found or error stating path."
                ))
            continue
        
        try:
            total, used, free = shutil.disk_usage(path_str)
            stats_list.append(StorageStats(
                path=path_str,
                total_bytes=total,
                used_bytes=used,
                free_bytes=free,
            ))
        except Exception as e:
            logger.error(f"Error getting disk usage for {path_str}: {e}")
            stats_list.append(StorageStats(
                path=path_str,
                total_bytes=0,
                used_bytes=0,
                free_bytes=0,
                error=str(e),
            ))
            
    return stats_list
