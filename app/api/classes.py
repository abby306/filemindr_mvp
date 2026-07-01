"""Class-catalog endpoints — list, create, and delete document classes.

The class catalog is what extraction classifies against (each class's `description`
is the signal the model uses). System classes ship seeded and are immutable; users
add custom classes per account. A new class is picked up by the *next* extraction —
existing documents are not retroactively re-classified.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select

from app.api.schemas import ClassCreate, ClassOut
from app.core.scoping import AccountScope, get_current_account
from app.db.models import Class, DocumentClass

router = APIRouter(prefix="/api/v1", tags=["classes"])


def _slugify(name: str) -> str:
    """Derive a URL/prompt-safe slug from a class name (lowercase, `_`-joined)."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _document_counts(scope: AccountScope) -> dict[uuid.UUID, int]:
    """Distinct document count per class for the active account (one grouped query)."""
    rows = scope.db.execute(
        select(DocumentClass.class_id, func.count(func.distinct(DocumentClass.document_id)))
        .where(DocumentClass.account_id == scope.account_id)
        .group_by(DocumentClass.class_id)
    ).all()
    return {class_id: n for class_id, n in rows}


@router.get("/classes", response_model=list[ClassOut])
def list_classes(scope: AccountScope = Depends(get_current_account)) -> list[ClassOut]:
    """List the account's classes (system first), each with its document count."""
    counts = _document_counts(scope)
    classes = scope.db.scalars(
        scope.select(Class).order_by(Class.is_system.desc(), Class.name.asc())
    ).all()
    return [
        ClassOut(
            id=c.id, slug=c.slug, name=c.name, description=c.description,
            is_system=c.is_system, document_count=counts.get(c.id, 0),
        )
        for c in classes
    ]


@router.post("/classes", response_model=ClassOut, status_code=status.HTTP_201_CREATED)
def create_class(
    body: ClassCreate, scope: AccountScope = Depends(get_current_account)
) -> ClassOut:
    """Create a custom class for the active account.

    Slug is derived from the name; a good `description` drives classification quality.
    409 if the slug already exists (including collisions with a system class).
    """
    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_name", "message": "Name must contain a letter or digit."},
        )
    if scope.db.scalar(scope.select(Class).where(Class.slug == slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "class_exists", "message": f"A class with slug '{slug}' already exists."},
        )
    cls = Class(
        account_id=scope.account_id, slug=slug, name=body.name.strip(),
        description=body.description, is_system=False,
    )
    scope.db.add(cls)
    scope.db.commit()
    scope.db.refresh(cls)
    return ClassOut(
        id=cls.id, slug=cls.slug, name=cls.name, description=cls.description,
        is_system=cls.is_system, document_count=0,
    )


@router.delete("/classes/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(
    class_id: uuid.UUID, scope: AccountScope = Depends(get_current_account)
) -> Response:
    """Delete a custom class (system classes are immutable).

    404 if the class isn't in the active account; 409 if it's a system class. Deleting
    cascades its `document_classes` links (documents keep their other classes).
    """
    cls = scope.db.scalar(scope.select(Class).where(Class.id == class_id))
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Class not found."},
        )
    if cls.is_system:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "system_immutable", "message": "System classes cannot be deleted."},
        )
    scope.db.delete(cls)
    scope.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
