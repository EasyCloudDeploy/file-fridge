"""API routes for criteria management."""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Criteria, CriterionType, MonitoredPath
from app.schemas import Criteria as CriteriaSchema
from app.schemas import CriteriaCreate, CriteriaUpdate
from app.utils.network_detection import check_atime_availability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/criteria", tags=["criteria"])


@router.get("/path/{path_id}", response_model=List[CriteriaSchema])
def list_criteria(path_id: int, db: Session = Depends(get_db)):
    """List all criteria for a path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )
    return path.criteria


@router.post("/path/{path_id}", response_model=CriteriaSchema, status_code=status.HTTP_201_CREATED)
def create_criteria(path_id: int, criteria: CriteriaCreate, db: Session = Depends(get_db)):
    """Create a new criterion for a path."""
    path = db.query(MonitoredPath).filter(MonitoredPath.id == path_id).first()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Path with id {path_id} not found"
        )

    # Validate: Check if atime criteria is being created and cold storage is a network mount
    if criteria.criterion_type == CriterionType.ATIME and criteria.enabled:
        atime_available, error_msg = check_atime_availability(path.cold_storage_path)
        if not atime_available:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    db_criteria = Criteria(path_id=path_id, **criteria.model_dump())
    db.add(db_criteria)
    db.commit()
    db.refresh(db_criteria)

    # Re-validate path configuration to update error state
    from app.routers.api.paths import validate_path_configuration

    try:
        validate_path_configuration(path, db)
    except HTTPException:
        pass  # Error already set on path

    return db_criteria


@router.get("/{criteria_id}", response_model=CriteriaSchema)
def get_criteria(criteria_id: int, db: Session = Depends(get_db)):
    """Get a specific criterion."""
    criteria = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found",
        )
    return criteria


@router.put("/{criteria_id}", response_model=CriteriaSchema)
def update_criteria(
    criteria_id: int, criteria_update: CriteriaUpdate, db: Session = Depends(get_db)
):
    """Update a criterion."""
    criteria = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found",
        )

    path = criteria.path

    # Check if we're enabling atime criteria or changing to atime type
    update_data = criteria_update.model_dump(exclude_unset=True)
    will_be_atime = (
        update_data.get("criterion_type", criteria.criterion_type) == CriterionType.ATIME
    )
    will_be_enabled = update_data.get(
        "enabled", criteria.enabled if "enabled" not in update_data else True
    )

    # Validate: Check if atime criteria is being enabled and cold storage is a network mount
    if will_be_atime and will_be_enabled:
        atime_available, error_msg = check_atime_availability(path.cold_storage_path)
        if not atime_available:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    for field, value in update_data.items():
        setattr(criteria, field, value)

    db.commit()
    db.refresh(criteria)

    # Re-validate path configuration to update error state
    from app.routers.api.paths import validate_path_configuration

    try:
        validate_path_configuration(path, db)
    except HTTPException:
        pass  # Error already set on path

    return criteria


@router.delete("/{criteria_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_criteria(criteria_id: int, db: Session = Depends(get_db)):
    """Delete a criterion. If it's the last criterion, all files are moved back from cold storage."""
    criteria = db.query(Criteria).filter(Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Criteria with id {criteria_id} not found",
        )

    path_id = criteria.path_id

    # Delete the criterion
    db.delete(criteria)
    db.commit()

    # Check if there are any remaining enabled criteria for this path
    remaining_criteria = (
        db.query(Criteria).filter(Criteria.path_id == path_id, Criteria.enabled).count()
    )

    # If no criteria remain, reverse all operations (move files back)
    if remaining_criteria == 0:
        from app.services.path_reverser import PathReverser

        logger.info(f"No criteria remaining for path {path_id}, reversing all file operations")
        results = PathReverser.reverse_path_operations(path_id, db)
        if results["errors"]:
            logger.warning(f"Some errors occurred while reversing operations: {results['errors']}")
        logger.info(f"Reversed {results['files_reversed']} files for path {path_id}")
