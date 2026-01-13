"""API routes for tag management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

from app.database import get_db
from app.models import Tag, FileTag, FileInventory
from app.schemas import Tag as TagSchema, TagCreate, TagUpdate, FileTagCreate, FileTagResponse, TagWithCount

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


@router.get("", response_model=List[TagWithCount])
def list_tags(db: Session = Depends(get_db)):
    """List all tags with file counts."""
    tags_with_counts = db.query(
        Tag,
        func.count(FileTag.id).label('file_count')
    ).outerjoin(FileTag, Tag.id == FileTag.tag_id)\
     .group_by(Tag.id)\
     .order_by(Tag.name)\
     .all()

    return [
        {
            'id': tag.id,
            'name': tag.name,
            'description': tag.description,
            'color': tag.color,
            'created_at': tag.created_at,
            'file_count': file_count
        }
        for tag, file_count in tags_with_counts
    ]


@router.post("", response_model=TagSchema, status_code=status.HTTP_201_CREATED)
def create_tag(tag: TagCreate, db: Session = Depends(get_db)):
    """Create a new tag."""
    existing_tag = db.query(Tag).filter(Tag.name == tag.name).first()
    if existing_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tag with name '{tag.name}' already exists"
        )

    new_tag = Tag(name=tag.name, description=tag.description, color=tag.color)
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    return new_tag


@router.get("/{tag_id}", response_model=TagSchema)
def get_tag(tag_id: int, db: Session = Depends(get_db)):
    """Get a specific tag by ID."""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )
    return tag


@router.patch("/{tag_id}", response_model=TagSchema)
def update_tag(tag_id: int, tag_update: TagUpdate, db: Session = Depends(get_db)):
    """Update a tag."""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )

    update_data = tag_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tag, field, value)

    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    """Delete a tag."""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )

    db.delete(tag)
    db.commit()
    return None


@router.post("/files/{file_id}/tags", response_model=FileTagResponse, status_code=status.HTTP_201_CREATED)
def add_tag_to_file(file_id: int, tag_data: FileTagCreate, db: Session = Depends(get_db)):
    """Add a tag to a file."""
    file_inv = db.query(FileInventory).filter(FileInventory.id == file_id).first()
    if not file_inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with ID {file_id} not found"
        )

    tag = db.query(Tag).filter(Tag.id == tag_data.tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_data.tag_id} not found"
        )

    existing = db.query(FileTag).filter(
        FileTag.file_id == file_id,
        FileTag.tag_id == tag_data.tag_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File already has this tag"
        )

    file_tag = FileTag(file_id=file_id, tag_id=tag_data.tag_id, tagged_by=tag_data.tagged_by)
    db.add(file_tag)
    db.commit()
    db.refresh(file_tag)
    return file_tag


@router.delete("/files/{file_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_tag_from_file(file_id: int, tag_id: int, db: Session = Depends(get_db)):
    """Remove a tag from a file."""
    file_tag = db.query(FileTag).filter(
        FileTag.file_id == file_id,
        FileTag.tag_id == tag_id
    ).first()

    if not file_tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File tag not found"
        )

    db.delete(file_tag)
    db.commit()
    return None


@router.get("/files/{file_id}/tags", response_model=List[FileTagResponse])
def get_file_tags(file_id: int, db: Session = Depends(get_db)):
    """Get all tags for a file."""
    file_inv = db.query(FileInventory).filter(FileInventory.id == file_id).first()
    if not file_inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with ID {file_id} not found"
        )

    return db.query(FileTag).filter(FileTag.file_id == file_id).all()
