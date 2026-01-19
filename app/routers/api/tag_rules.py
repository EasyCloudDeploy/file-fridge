# ruff: noqa: B008
"""API routes for tag rule management."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Tag
from app.models import TagRule as TagRuleModel
from app.schemas import TagRule as TagRuleSchema
from app.schemas import TagRuleCreate, TagRuleUpdate

router = APIRouter(prefix="/api/v1/tag-rules", tags=["tag-rules"])


@router.get("", response_model=List[TagRuleSchema])
def list_tag_rules(db: Session = Depends(get_db)):
    """List all tag rules."""
    return (
        db.query(TagRuleModel)
        .order_by(TagRuleModel.priority.desc(), TagRuleModel.created_at.asc())
        .all()
    )


@router.post("", response_model=TagRuleSchema, status_code=status.HTTP_201_CREATED)
def create_tag_rule(rule: TagRuleCreate, db: Session = Depends(get_db)):
    """Create a new tag rule."""
    tag = db.query(Tag).filter(Tag.id == rule.tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag with ID {rule.tag_id} not found"
        )

    new_rule = TagRuleModel(
        tag_id=rule.tag_id,
        criterion_type=rule.criterion_type,
        operator=rule.operator,
        value=rule.value,
        enabled=rule.enabled,
        priority=rule.priority,
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule


@router.get("/{rule_id}", response_model=TagRuleSchema)
def get_tag_rule(rule_id: int, db: Session = Depends(get_db)):
    """Get a specific tag rule by ID."""
    rule = db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag rule with ID {rule_id} not found"
        )
    return rule


@router.patch("/{rule_id}", response_model=TagRuleSchema)
def update_tag_rule(rule_id: int, rule_update: TagRuleUpdate, db: Session = Depends(get_db)):
    """Update a tag rule."""
    rule = db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag rule with ID {rule_id} not found"
        )

    update_data = rule_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag_rule(rule_id: int, db: Session = Depends(get_db)):
    """Delete a tag rule."""
    rule = db.query(TagRuleModel).filter(TagRuleModel.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag rule with ID {rule_id} not found"
        )

    db.delete(rule)
    db.commit()


@router.post("/apply", status_code=status.HTTP_200_OK)
def apply_tag_rules(db: Session = Depends(get_db)):
    """Apply all enabled tag rules to all files in inventory."""
    from app.services.tag_rule_service import TagRuleService

    service = TagRuleService(db)
    return service.apply_all_rules()
