"""Document ingest + listing endpoints.

`POST /documents` is the web-upload path: validate, content-address, dedup, and
persist at status `received`, then kick OCR off as a background task. Reads are
account-scoped through `AccountScope`, so no endpoint can see another account's
documents.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, tuple_

from app.api.schemas import (
    ClassCardOut,
    DateCardOut,
    DocumentCardOut,
    DocumentListOut,
    DocumentOut,
    EntitiesCardOut,
    TypedFactCardOut,
)
from app.core.scoping import AccountScope, get_current_account
from app.db.models import (
    Class,
    Document,
    DocumentClass,
    DocumentDate,
    DocumentEntity,
    DocumentFact,
    Entity,
    TypedFact,
)
from app.services import ocr
from app.services.events import record_event
from app.services.storage import save_upload

router = APIRouter(prefix="/api/v1", tags=["documents"])

# Map extensions to MIME for clients that send a blank/generic content type.
_MIME_BY_EXT = {
    ".pdf": ocr.PDF_MIME,
    ".docx": ocr.DOCX_MIME,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _resolve_mime(content_type: str | None, filename: str | None) -> str | None:
    """Pick a supported MIME type from the header, falling back to the extension."""
    if content_type in ocr.ALLOWED_MIME_TYPES:
        return content_type
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[1].lower()
        return _MIME_BY_EXT.get(ext)
    return None


def _encode_cursor(document: Document) -> str:
    payload = json.dumps([document.created_at.isoformat(), str(document.id)])
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        created_iso, doc_id = json.loads(base64.urlsafe_b64decode(cursor).decode())
        return dt.datetime.fromisoformat(created_iso), uuid.UUID(doc_id)
    except (ValueError, binascii.Error, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_cursor", "message": "Invalid pagination cursor."},
        )


@router.post("/documents", response_model=DocumentOut)
async def upload_document(
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scope: AccountScope = Depends(get_current_account),
) -> DocumentOut:
    """Accept a file, dedup by content hash, and start OCR in the background."""
    mime_type = _resolve_mime(file.content_type, file.filename)
    if mime_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "unsupported_media_type",
                "message": "Only PDF, PNG, JPEG, and DOCX files are accepted.",
            },
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "empty_file", "message": "Uploaded file is empty."},
        )

    ext = ocr.extension_for(mime_type, file.filename)
    stored = save_upload(content, scope.account_id, ext)

    # Dedup: identical (account, file_hash) returns the existing document.
    existing = scope.db.scalar(
        scope.select(Document).where(Document.file_hash == stored.file_hash)
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return DocumentOut.model_validate(existing)

    document = Document(
        account_id=scope.account_id,
        uploaded_by=scope.user.id,
        source="web_upload",
        original_filename=file.filename or f"upload{ext}",
        mime_type=mime_type,
        byte_size=stored.byte_size,
        file_hash=stored.file_hash,
        storage_path=stored.storage_path,
        status="received",
    )
    scope.db.add(document)
    scope.db.flush()
    record_event(
        scope.db,
        account_id=scope.account_id,
        document_id=document.id,
        stage="received",
        status="succeeded",
        detail={"source": "web_upload", "byte_size": stored.byte_size},
    )
    scope.db.commit()
    scope.db.refresh(document)

    background_tasks.add_task(ocr.run_ocr, document.id, scope.account_id)

    response.status_code = status.HTTP_201_CREATED
    return DocumentOut.model_validate(document)


@router.get("/documents", response_model=DocumentListOut)
def list_documents(
    scope: AccountScope = Depends(get_current_account),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> DocumentListOut:
    """List the active account's documents, newest first (keyset paginated)."""
    query = scope.select(Document).order_by(
        Document.created_at.desc(), Document.id.desc()
    )
    if status_filter is not None:
        query = query.where(Document.status == status_filter)
    if cursor is not None:
        cur_created, cur_id = _decode_cursor(cursor)
        query = query.where(
            tuple_(Document.created_at, Document.id) < (cur_created, cur_id)
        )

    rows = scope.db.scalars(query.limit(limit + 1)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None
    return DocumentListOut(
        items=[DocumentOut.model_validate(d) for d in page],
        next_cursor=next_cursor,
    )


@router.get("/documents/{document_id}", response_model=DocumentCardOut)
def get_document(
    document_id: uuid.UUID,
    scope: AccountScope = Depends(get_current_account),
) -> DocumentCardOut:
    """Fetch one document's full card by id, scoped to the active account.

    Returns 404 for another account's document. Card sections are empty until
    extraction has run.
    """
    document = scope.db.scalar(
        scope.select(Document).where(Document.id == document_id)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Document not found."},
        )
    return _build_card(scope, document)


def _build_card(scope: AccountScope, document: Document) -> DocumentCardOut:
    """Assemble a `DocumentCardOut` from the extracted card tables (scoped)."""
    classes = scope.db.execute(
        scope.select(DocumentClass)
        .where(DocumentClass.document_id == document.id)
        .join(Class, Class.id == DocumentClass.class_id)
        .with_only_columns(Class.slug, Class.name, DocumentClass.confidence)
        .order_by(DocumentClass.confidence.desc().nullslast())
    ).all()

    entity_rows = scope.db.execute(
        scope.select(DocumentEntity)
        .where(DocumentEntity.document_id == document.id)
        .join(Entity, Entity.id == DocumentEntity.entity_id)
        .with_only_columns(Entity.type, Entity.name)
    ).all()
    entities = EntitiesCardOut()
    _entity_bucket = {
        "person": entities.people,
        "organization": entities.organizations,
        "place": entities.places,
    }
    for type_, name in entity_rows:
        _entity_bucket[type_].append(name)

    dates = scope.db.scalars(
        scope.select(DocumentDate)
        .where(DocumentDate.document_id == document.id)
        .order_by(DocumentDate.value.asc().nullslast())
    ).all()

    typed_facts = scope.db.scalars(
        scope.select(TypedFact)
        .where(TypedFact.document_id == document.id)
        .order_by(TypedFact.label.asc())
    ).all()

    fact_count = scope.db.scalar(
        scope.select(DocumentFact)
        .where(DocumentFact.document_id == document.id)
        .with_only_columns(func.count())
    ) or 0

    card = DocumentCardOut.model_validate(document)
    card.classes = [
        ClassCardOut(slug=slug, name=name, confidence=confidence)
        for slug, name, confidence in classes
    ]
    card.entities = entities
    card.dates = [
        DateCardOut(value=d.value, raw_text=d.raw_text, role=d.role) for d in dates
    ]
    card.typed_facts = [
        TypedFactCardOut(
            label=f.label,
            value=f.value,
            value_numeric=float(f.value_numeric) if f.value_numeric is not None else None,
            type=f.value_type,
            unit=f.unit,
            page=f.page,
        )
        for f in typed_facts
    ]
    card.fact_count = fact_count
    return card
