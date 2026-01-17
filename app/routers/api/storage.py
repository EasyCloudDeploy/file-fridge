"""API routes for storage management."""

import logging
import os
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ColdStorageLocation, FileInventory, FileRecord
from app.schemas import ColdStorageLocation as ColdStorageLocationSchema
from app.schemas import (
    ColdStorageLocationCreate,
    ColdStorageLocationUpdate,
    ColdStorageLocationWithStats,
    StorageStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


@router.get("/stats", response_model=List[StorageStats])
def get_storage_stats(db: Session = Depends(get_db)):
    """Get storage statistics for all cold storage locations."""
    locations = db.query(ColdStorageLocation).all()

    unique_volumes = {}
    for location in locations:
        path_str = location.path
        try:
            # Get the device ID for the path
            device_id = os.stat(path_str).st_dev
            if device_id not in unique_volumes:
                unique_volumes[device_id] = path_str
        except FileNotFoundError:
            # Handle cases where the path doesn't exist
            if "not_found" not in unique_volumes:
                unique_volumes["not_found"] = []
            unique_volumes["not_found"].append(path_str)
        except Exception as e:
            # Handle other potential errors
            logger.exception(f"Error stating path {path_str}: {e}")
            if "error" not in unique_volumes:
                unique_volumes["error"] = []
            unique_volumes["error"].append(path_str)

    stats_list = []
    for device_id, path_str in unique_volumes.items():
        if device_id in {"not_found", "error"}:
            for p in path_str:
                stats_list.append(
                    StorageStats(
                        path=p,
                        total_bytes=0,
                        used_bytes=0,
                        free_bytes=0,
                        error="Path not found or error stating path.",
                    )
                )
            continue

        try:
            total, used, free = shutil.disk_usage(path_str)
            stats_list.append(
                StorageStats(
                    path=path_str,
                    total_bytes=total,
                    used_bytes=used,
                    free_bytes=free,
                )
            )
        except Exception as e:
            logger.exception(f"Error getting disk usage for {path_str}: {e}")
            stats_list.append(
                StorageStats(
                    path=path_str,
                    total_bytes=0,
                    used_bytes=0,
                    free_bytes=0,
                    error=str(e),
                )
            )

    return stats_list


# ColdStorageLocation CRUD endpoints


@router.get("/locations", response_model=List[ColdStorageLocationWithStats])
def list_storage_locations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all cold storage locations."""
    locations = db.query(ColdStorageLocation).offset(skip).limit(limit).all()

    locations_with_stats = []
    for loc in locations:
        locations_with_stats.append(
            ColdStorageLocationWithStats(**loc.__dict__, path_count=len(loc.paths))
        )
    return locations_with_stats


@router.post(
    "/locations", response_model=ColdStorageLocationSchema, status_code=status.HTTP_201_CREATED
)
def create_storage_location(location: ColdStorageLocationCreate, db: Session = Depends(get_db)):
    """Create a new cold storage location."""
    # Check for duplicate name
    existing_name = (
        db.query(ColdStorageLocation).filter(ColdStorageLocation.name == location.name).first()
    )
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location with name '{location.name}' already exists",
        )

    # Check for duplicate path
    existing_path = (
        db.query(ColdStorageLocation).filter(ColdStorageLocation.path == location.path).first()
    )
    if existing_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Storage location with path '{location.path}' already exists",
        )

    # Validate path exists
    path_obj = Path(location.path)
    if not path_obj.exists():
        try:
            path_obj.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot create storage location path: {e!s}",
            )

    if not path_obj.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {location.path}",
        )

    db_location = ColdStorageLocation(**location.model_dump())
    db.add(db_location)
    db.commit()
    db.refresh(db_location)

    return db_location


@router.get("/locations/{location_id}", response_model=ColdStorageLocationSchema)
def get_storage_location(location_id: int, db: Session = Depends(get_db)):
    """Get a specific cold storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )
    return location


@router.put("/locations/{location_id}", response_model=ColdStorageLocationSchema)
def update_storage_location(
    location_id: int, location_update: ColdStorageLocationUpdate, db: Session = Depends(get_db)
):
    """Update a cold storage location."""
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )

    update_data = location_update.model_dump(exclude_unset=True)

    # Check for duplicate name if name is being updated
    if "name" in update_data:
        existing = (
            db.query(ColdStorageLocation)
            .filter(
                ColdStorageLocation.name == update_data["name"],
                ColdStorageLocation.id != location_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Storage location with name '{update_data['name']}' already exists",
            )

    # Check for duplicate path if path is being updated
    if "path" in update_data:
        existing = (
            db.query(ColdStorageLocation)
            .filter(
                ColdStorageLocation.path == update_data["path"],
                ColdStorageLocation.id != location_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Storage location with path '{update_data['path']}' already exists",
            )

        # Validate new path
        path_obj = Path(update_data["path"])
        if not path_obj.exists():
            try:
                path_obj.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create storage location path: {e!s}",
                )

        if not path_obj.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {update_data['path']}",
            )

    # Update fields
    for field, value in update_data.items():
        setattr(location, field, value)

    db.commit()
    db.refresh(location)

    return location


@router.delete("/locations/{location_id}", status_code=status.HTTP_200_OK)
def delete_storage_location(
    location_id: int,
    force: bool = Query(False, description="Force delete the location even if it's not empty"),
    db: Session = Depends(get_db),
):
    """
    Delete a cold storage location.

    - If `force` is False, this will fail if the location is still associated with any monitored paths.
    - If `force` is True, it will remove all associated file records and attempt to delete the files from storage.
      This is useful for corrupted or lost drives.
    """
    location = db.query(ColdStorageLocation).filter(ColdStorageLocation.id == location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage location with id {location_id} not found",
        )

    # Standard delete: Check if location is still in use by monitored paths
    if not force and location.paths:
        path_names = [p.name for p in location.paths]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete storage location '{location.name}' because it is still associated with "
            f"{len(location.paths)} monitored path(s): {', '.join(path_names)}",
        )

    # Force delete: Clean up all associated data
    if force:
        logger.info(f"Force deleting storage location '{location.name}' (ID: {location_id})")

        # 1. Find all files in this storage location
        # We need to check both FileInventory and FileRecord for paths
        inventory_files = (
            db.query(FileInventory).filter(FileInventory.file_path.like(f"{location.path}%")).all()
        )
        file_records = (
            db.query(FileRecord)
            .filter(FileRecord.cold_storage_path.like(f"{location.path}%"))
            .all()
        )

        # 2. Delete file records from the database
        for record in file_records:
            db.delete(record)

        for inv_file in inventory_files:
            db.delete(inv_file)

        # 3. Attempt to delete the actual files and directory from the filesystem
        try:
            if os.path.exists(location.path):
                logger.info(f"Deleting files and directory: {location.path}")
                shutil.rmtree(location.path)
        except FileNotFoundError:
            logger.warning(f"Path not found, proceeding with DB deletion: {location.path}")
        except Exception as e:
            logger.exception(
                f"Error deleting storage directory '{location.path}': {e}. "
                f"Manual cleanup may be required."
            )
            # We don't re-raise, to allow DB cleanup to proceed

        # 4. Disassociate monitored paths
        location.paths.clear()

        db.commit()  # Commit record deletions and path disassociation

    # Delete the location itself
    db.delete(location)
    db.commit()

    return {"message": f"Storage location '{location.name}' deleted successfully"}
