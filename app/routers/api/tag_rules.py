"""API routes for tag rule management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.models import TagRule as TagRuleModel, Tag
from app.schemas import TagRule as TagRuleSchema, TagRuleCreate, TagRuleUpdate

router = APIRouter(prefix="/api/v1/tag-rules", tags=["tag-rules"])


@router.get("", response_model=List[TagRuleSchema])
async def list_tag_rules(db: Session = Depends(get_db)):
    """List all tag rules."""
    rules = await run_in_threadpool(_get_all_tag_rules, db)
    return rules


def _get_all_tag_rules(db: Session) -> List[TagRuleModel]:
    """Get all tag rules (runs in thread pool)."""
    return db.query(TagRuleModel).order_by(
        TagRuleModel.priority.desc(),
        TagRuleModel.created_at.asc()
    ).all()


@router.post("", response_model=TagRuleSchema, status_code=status.HTTP_201_CREATED)
async def create_tag_rule(rule: TagRuleCreate, db: Session = Depends(get_db)):
    """Create a new tag rule."""
    result = await run_in_threadpool(_create_tag_rule, db, rule)
    return result


def _create_tag_rule(db: Session, rule_data: TagRuleCreate) -> TagRuleModel:
    """Create tag rule (runs in thread pool)."""
    # Check if tag exists
    tag = db.query(Tag).filter(Tag.id == rule_data.tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {rule_data.tag_id} not found"
        )

    # Create new rule
    new_rule = TagRuleModel(
        tag_id=rule_data.tag_id,
        criterion_type=rule_data.criterion_type,
        operator=rule_data.operator,
        value=rule_data.value,
        enabled=rule_data.enabled,
        priority=rule_data.priority
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule


@router.get("/{rule_id}", response_model=TagRuleSchema)
async def get_tag_rule(rule_id: int, db: Session = Depends(get_db)):
    """Get a specific tag rule by ID."""
    rule = await run_in_threadpool(_get_tag_rule_by_id, db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag rule with ID {rule_id} not found"
        )
    return rule


def _get_tag_rule_by_id(db: Session, rule_id: int) -> TagRuleModel:
    """Get tag rule by ID (runs in thread pool)."""
    return db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()


@router.patch("/{rule_id}", response_model=TagRuleSchema)
async def update_tag_rule(rule_id: int, rule_update: TagRuleUpdate, db: Session = Depends(get_db)):
    """Update a tag rule."""
    rule = await run_in_threadpool(_update_tag_rule, db, rule_id, rule_update)
    return rule


def _update_tag_rule(db: Session, rule_id: int, rule_update: TagRuleUpdate) -> TagRuleModel:
    """Update tag rule (runs in thread pool)."""
    rule = db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag rule with ID {rule_id} not found"
        )

    # Update fields
    update_data = rule_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_rule(rule_id: int, db: Session = Depends(get_db)):
    """Delete a tag rule."""
    await run_in_threadpool(_delete_tag_rule, db, rule_id)
    return None


def _delete_tag_rule(db: Session, rule_id: int):
    """Delete tag rule (runs in thread pool)."""
    rule = db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag rule with ID {rule_id} not found"
        )

    db.delete(rule)
    db.commit()


@router.post("/apply", status_code=status.HTTP_200_OK)
async def apply_tag_rules(db: Session = Depends(get_db)):
    """Apply all enabled tag rules to all files in inventory."""
    result = await run_in_threadpool(_apply_all_tag_rules, db)
    return result


def _apply_all_tag_rules(db: Session) -> dict:
    """Apply tag rules to files (runs in thread pool)."""
    from app.services.tag_rule_service import TagRuleService

    service = TagRuleService(db)
    result = service.apply_all_rules()
    return result
