"""API routes for statistics."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Dict
from datetime import datetime, timedelta
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.models import FileRecord, MonitoredPath, FileInventory, StorageType, Criteria, PinnedFile
from app.schemas import Statistics, FileRecord as FileRecordSchema, DetailedStatistics
from app.config import settings
from app.services.stats_cleanup import stats_cleanup_service

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("", response_model=Statistics)
async def get_statistics(db: Session = Depends(get_db)):
    """Get overall statistics."""
    # Run database queries in thread pool to avoid blocking the event loop
    stats = await run_in_threadpool(_calculate_statistics, db)
    return stats


def _calculate_statistics(db: Session) -> Statistics:
    """Calculate statistics (runs in thread pool)."""
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


@router.get("/detailed", response_model=DetailedStatistics)
async def get_detailed_statistics(
    days: int = None,
    db: Session = Depends(get_db)
):
    """Get comprehensive statistics with detailed metrics and trends."""
    if days is None:
        days = settings.stats_retention_days
    stats = await run_in_threadpool(_calculate_detailed_statistics, db, days)
    return stats


@router.post("/cleanup")
async def cleanup_old_stats(db: Session = Depends(get_db)):
    """Manually trigger cleanup of old statistics data."""
    result = await run_in_threadpool(stats_cleanup_service.cleanup_old_records, db)
    return result


@router.get("/aggregated")
async def get_aggregated_stats(
    period: str = "daily",  # daily, weekly, monthly
    days: int = 30,
    db: Session = Depends(get_db)
):
    """Get time-based aggregated statistics."""
    # Run database query in thread pool to avoid blocking the event loop
    result = await run_in_threadpool(_calculate_aggregated_stats, db, period, days)
    return result


def _calculate_detailed_statistics(db: Session, days: int) -> DetailedStatistics:
    """Calculate comprehensive detailed statistics (runs in thread pool)."""
    now = datetime.now()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    cutoff_period = now - timedelta(days=days)

    # Capacity metrics
    total_files_moved = db.query(func.count(FileRecord.id)).scalar() or 0
    total_size_moved = db.query(func.sum(FileRecord.file_size)).scalar() or 0

    # Hot/Cold storage from inventory
    total_files_hot = db.query(func.count(FileInventory.id)).filter(
        FileInventory.storage_type == StorageType.HOT
    ).scalar() or 0

    total_files_cold = db.query(func.count(FileInventory.id)).filter(
        FileInventory.storage_type == StorageType.COLD
    ).scalar() or 0

    total_size_hot = db.query(func.sum(FileInventory.file_size)).filter(
        FileInventory.storage_type == StorageType.HOT
    ).scalar() or 0

    total_size_cold = db.query(func.sum(FileInventory.file_size)).filter(
        FileInventory.storage_type == StorageType.COLD
    ).scalar() or 0

    # Space saved (total moved to cold storage)
    space_saved = total_size_moved

    # Average file size
    average_file_size = int(total_size_moved / total_files_moved) if total_files_moved > 0 else 0

    # Performance metrics - Last 24 hours
    files_moved_24h = db.query(func.count(FileRecord.id)).filter(
        FileRecord.moved_at >= cutoff_24h
    ).scalar() or 0

    size_moved_24h = db.query(func.sum(FileRecord.file_size)).filter(
        FileRecord.moved_at >= cutoff_24h
    ).scalar() or 0

    # Performance metrics - Last 7 days
    files_moved_7d = db.query(func.count(FileRecord.id)).filter(
        FileRecord.moved_at >= cutoff_7d
    ).scalar() or 0

    size_moved_7d = db.query(func.sum(FileRecord.file_size)).filter(
        FileRecord.moved_at >= cutoff_7d
    ).scalar() or 0

    # Calculate averages
    total_days_with_data = db.query(
        func.count(func.distinct(func.date(FileRecord.moved_at)))
    ).filter(FileRecord.moved_at >= cutoff_period).scalar() or 1

    average_files_per_day = float(
        db.query(func.count(FileRecord.id)).filter(
            FileRecord.moved_at >= cutoff_period
        ).scalar() or 0
    ) / max(total_days_with_data, 1)

    average_size_per_day = float(
        db.query(func.sum(FileRecord.file_size)).filter(
            FileRecord.moved_at >= cutoff_period
        ).scalar() or 0
    ) / max(total_days_with_data, 1)

    # Operational metrics
    total_paths = db.query(func.count(MonitoredPath.id)).scalar() or 0
    active_paths = db.query(func.count(MonitoredPath.id)).filter(
        MonitoredPath.enabled == True
    ).scalar() or 0
    total_criteria = db.query(func.count(Criteria.id)).scalar() or 0
    pinned_files = db.query(func.count(PinnedFile.id)).scalar() or 0

    # Daily activity trend (last N days)
    daily_activity = []
    daily_stats = db.query(
        func.date(FileRecord.moved_at).label("date"),
        func.count(FileRecord.id).label("files_moved"),
        func.sum(FileRecord.file_size).label("size_moved")
    ).filter(
        FileRecord.moved_at >= cutoff_period
    ).group_by(
        func.date(FileRecord.moved_at)
    ).order_by(
        func.date(FileRecord.moved_at)
    ).all()

    for stat in daily_stats:
        daily_activity.append({
            "date": str(stat.date),
            "files_moved": stat.files_moved or 0,
            "size_moved": stat.size_moved or 0
        })

    # Storage trend - track hot/cold storage over time
    # For now, we'll provide current snapshot as we don't have historical inventory tracking
    storage_trend = [{
        "date": str(now.date()),
        "hot_storage": total_size_hot,
        "cold_storage": total_size_cold
    }]

    # Top paths by file count
    top_paths_files = db.query(
        MonitoredPath.name,
        MonitoredPath.id,
        func.count(FileRecord.id).label("file_count"),
        func.sum(FileRecord.file_size).label("total_size")
    ).join(
        FileRecord, FileRecord.path_id == MonitoredPath.id
    ).group_by(
        MonitoredPath.id, MonitoredPath.name
    ).order_by(
        func.count(FileRecord.id).desc()
    ).limit(5).all()

    top_paths_by_files = [
        {
            "path_name": p.name,
            "path_id": p.id,
            "file_count": p.file_count,
            "total_size": p.total_size or 0
        }
        for p in top_paths_files
    ]

    # Top paths by size
    top_paths_size = db.query(
        MonitoredPath.name,
        MonitoredPath.id,
        func.count(FileRecord.id).label("file_count"),
        func.sum(FileRecord.file_size).label("total_size")
    ).join(
        FileRecord, FileRecord.path_id == MonitoredPath.id
    ).group_by(
        MonitoredPath.id, MonitoredPath.name
    ).order_by(
        func.sum(FileRecord.file_size).desc()
    ).limit(5).all()

    top_paths_by_size = [
        {
            "path_name": p.name,
            "path_id": p.id,
            "file_count": p.file_count,
            "total_size": p.total_size or 0
        }
        for p in top_paths_size
    ]

    return DetailedStatistics(
        # Capacity metrics
        total_files_moved=total_files_moved,
        total_size_moved=total_size_moved,
        total_files_hot=total_files_hot,
        total_files_cold=total_files_cold,
        total_size_hot=total_size_hot,
        total_size_cold=total_size_cold,
        space_saved=space_saved,
        average_file_size=average_file_size,
        # Performance metrics
        files_moved_last_24h=files_moved_24h,
        files_moved_last_7d=files_moved_7d,
        size_moved_last_24h=size_moved_24h,
        size_moved_last_7d=size_moved_7d,
        average_files_per_day=average_files_per_day,
        average_size_per_day=average_size_per_day,
        # Operational metrics
        total_paths=total_paths,
        active_paths=active_paths,
        total_criteria=total_criteria,
        pinned_files=pinned_files,
        # Trend data
        daily_activity=daily_activity,
        storage_trend=storage_trend,
        # Top paths
        top_paths_by_files=top_paths_by_files,
        top_paths_by_size=top_paths_by_size
    )


def _calculate_aggregated_stats(db: Session, period: str, days: int) -> dict:
    """Calculate aggregated statistics (runs in thread pool)."""
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

