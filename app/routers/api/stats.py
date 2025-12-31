"""API routes for statistics."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Dict
from datetime import datetime, timedelta
from app.database import get_db
from app.models import FileRecord, MonitoredPath
from app.schemas import Statistics, FileRecord as FileRecordSchema

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("", response_model=Statistics)
def get_statistics(db: Session = Depends(get_db)):
    """Get overall statistics."""
    # Total files moved
    total_files = db.query(func.count(FileRecord.id)).scalar() or 0
    
    # Total size moved
    total_size = db.query(func.sum(FileRecord.file_size)).scalar() or 0
    
    # Files by path
    files_by_path = {}
    paths = db.query(MonitoredPath).all()
    for path in paths:
        count = db.query(func.count(FileRecord.id)).filter(
            FileRecord.path_id == path.id
        ).scalar() or 0
        size = db.query(func.sum(FileRecord.file_size)).filter(
            FileRecord.path_id == path.id
        ).scalar() or 0
        files_by_path[path.name] = {
            "count": count,
            "size": size or 0
        }
    
    # Recent activity (last 50 files)
    recent_activity = db.query(FileRecord).order_by(
        FileRecord.moved_at.desc()
    ).limit(50).all()
    
    return Statistics(
        total_files_moved=total_files,
        total_size_moved=total_size,
        files_by_path=files_by_path,
        recent_activity=recent_activity
    )


@router.get("/aggregated")
def get_aggregated_stats(
    period: str = "daily",  # daily, weekly, monthly
    days: int = 30,
    db: Session = Depends(get_db)
):
    """Get time-based aggregated statistics."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Group by time period
    if period == "daily":
        date_format = "%Y-%m-%d"
        group_by = func.date(FileRecord.moved_at)
    elif period == "weekly":
        date_format = "%Y-W%V"
        group_by = func.strftime("%Y-W%V", FileRecord.moved_at)
    elif period == "monthly":
        date_format = "%Y-%m"
        group_by = func.strftime("%Y-%m", FileRecord.moved_at)
    else:
        date_format = "%Y-%m-%d"
        group_by = func.date(FileRecord.moved_at)
    
    results = db.query(
        group_by.label("period"),
        func.count(FileRecord.id).label("count"),
        func.sum(FileRecord.file_size).label("size")
    ).filter(
        FileRecord.moved_at >= start_date
    ).group_by(
        group_by
    ).order_by(
        group_by
    ).all()
    
    return {
        "period": period,
        "data": [
            {
                "period": str(r.period),
                "count": r.count or 0,
                "size": r.size or 0
            }
            for r in results
        ]
    }

