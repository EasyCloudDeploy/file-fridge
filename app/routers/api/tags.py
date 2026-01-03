"""API routes for tag management."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.models import Tag, FileTag, FileInventory
from app.schemas import Tag as TagSchema, TagCreate, TagUpdate, FileTagCreate, FileTagResponse, TagWithCount

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


@router.get("", response_model=List[TagWithCount])
async def list_tags(db: Session = Depends(get_db)):
    """List all tags with file counts."""
    tags = await run_in_threadpool(_get_all_tags, db)
    return tags


def _get_all_tags(db: Session) -> List[dict]:
    """Get all tags with file counts (runs in thread pool)."""
    from sqlalchemy import func

    # Query tags with file counts
    tags_with_counts = db.query(
        Tag,
        func.count(FileTag.id).label('file_count')
    ).outerjoin(FileTag, Tag.id == FileTag.tag_id)\
     .group_by(Tag.id)\
     .order_by(Tag.name)\
     .all()

    # Convert to dict with file_count
    result = []
    for tag, file_count in tags_with_counts:
        tag_dict = {
            'id': tag.id,
            'name': tag.name,
            'description': tag.description,
            'color': tag.color,
            'created_at': tag.created_at,
            'file_count': file_count
        }
        result.append(tag_dict)

    return result


@router.post("", response_model=TagSchema, status_code=status.HTTP_201_CREATED)
async def create_tag(tag: TagCreate, db: Session = Depends(get_db)):
    """Create a new tag."""
    result = await run_in_threadpool(_create_tag, db, tag)
    return result


def _create_tag(db: Session, tag_data: TagCreate) -> Tag:
    """Create tag (runs in thread pool)."""
    # Check if tag already exists
    existing_tag = db.query(Tag).filter(Tag.name == tag_data.name).first()
    if existing_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tag with name '{tag_data.name}' already exists"
        )

    # Create new tag
    new_tag = Tag(
        name=tag_data.name,
        description=tag_data.description,
        color=tag_data.color
    )
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    return new_tag


@router.get("/{tag_id}", response_model=TagSchema)
async def get_tag(tag_id: int, db: Session = Depends(get_db)):
    """Get a specific tag by ID."""
    tag = await run_in_threadpool(_get_tag_by_id, db, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )
    return tag


def _get_tag_by_id(db: Session, tag_id: int) -> Tag:
    """Get tag by ID (runs in thread pool)."""
    return db.query(Tag).filter(Tag.id == tag_id).first()


@router.patch("/{tag_id}", response_model=TagSchema)
async def update_tag(tag_id: int, tag_update: TagUpdate, db: Session = Depends(get_db)):
    """Update a tag."""
    tag = await run_in_threadpool(_update_tag, db, tag_id, tag_update)
    return tag


def _update_tag(db: Session, tag_id: int, tag_update: TagUpdate) -> Tag:
    """Update tag (runs in thread pool)."""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )

    # Update fields
    update_data = tag_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tag, field, value)

    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    """Delete a tag."""
    await run_in_threadpool(_delete_tag, db, tag_id)
    return None


def _delete_tag(db: Session, tag_id: int):
    """Delete tag (runs in thread pool)."""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_id} not found"
        )

    db.delete(tag)
    db.commit()


@router.post("/files/{file_id}/tags", response_model=FileTagResponse, status_code=status.HTTP_201_CREATED)
async def add_tag_to_file(file_id: int, tag_data: FileTagCreate, db: Session = Depends(get_db)):
    """Add a tag to a file."""
    result = await run_in_threadpool(_add_tag_to_file, db, file_id, tag_data)
    return result


def _add_tag_to_file(db: Session, file_id: int, tag_data: FileTagCreate) -> FileTag:
    """Add tag to file (runs in thread pool)."""
    # Check if file exists
    file_inv = db.query(FileInventory).filter(FileInventory.id == file_id).first()
    if not file_inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with ID {file_id} not found"
        )

    # Check if tag exists
    tag = db.query(Tag).filter(Tag.id == tag_data.tag_id).first()
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag with ID {tag_data.tag_id} not found"
        )

    # Check if file already has this tag
    existing = db.query(FileTag).filter(
        FileTag.file_id == file_id,
        FileTag.tag_id == tag_data.tag_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File already has this tag"
        )

    # Create file tag
    file_tag = FileTag(
        file_id=file_id,
        tag_id=tag_data.tag_id,
        tagged_by=tag_data.tagged_by
    )
    db.add(file_tag)
    db.commit()
    db.refresh(file_tag)
    return file_tag


@router.delete("/files/{file_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag_from_file(file_id: int, tag_id: int, db: Session = Depends(get_db)):
    """Remove a tag from a file."""
    await run_in_threadpool(_remove_tag_from_file, db, file_id, tag_id)
    return None


def _remove_tag_from_file(db: Session, file_id: int, tag_id: int):
    """Remove tag from file (runs in thread pool)."""
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


@router.get("/files/{file_id}/tags", response_model=List[FileTagResponse])
async def get_file_tags(file_id: int, db: Session = Depends(get_db)):
    """Get all tags for a file."""
    tags = await run_in_threadpool(_get_file_tags, db, file_id)
    return tags


def _get_file_tags(db: Session, file_id: int) -> List[FileTag]:
    """Get file tags (runs in thread pool)."""
    # Check if file exists
    file_inv = db.query(FileInventory).filter(FileInventory.id == file_id).first()
    if not file_inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with ID {file_id} not found"
        )

    return db.query(FileTag).filter(FileTag.file_id == file_id).all()
