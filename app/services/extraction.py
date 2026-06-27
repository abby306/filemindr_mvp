"""Structured extraction — turn OCR text into a document card + atomic facts.

One cheap structured-output LLM pass (DeepSeek, via the OpenAI-compatible client)
reads `documents.ocr_text` and returns JSON: a title/summary, multi-label
classes, entities, dates, typed facts, and self-contained atomic facts. The raw
response is saved to `documents.extraction_raw`; the parsed result is fanned out
into the card tables (`document_classes`, `entities`/`document_entities`,
`document_dates`, `typed_facts`) and the primary retrieval unit
(`document_facts`). Provenance (page + best-effort bbox) is pulled from the OCR
cache artifact written in the OCR phase.

Design notes:
  * `parse_extraction` is pure and lenient — it never trusts the model's shapes;
    bad enums fall back to defaults and unparseable dates become null, so a noisy
    response degrades gracefully instead of failing the whole document.
  * `call_extraction_model` is the only network seam; tests monkeypatch it to run
    entirely offline.
  * `run_extraction` opens its own session (it runs as a background task chained
    after OCR), is account-scoped throughout, and is idempotent: a re-run clears
    the previous card before writing a fresh one.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
from app.db.session import SessionLocal
from app.services import ocr
from app.services.events import record_event

# --- routing thresholds ----------------------------------------------------
# Below this top-class confidence (or with no class at all) a document lands in
# `needs_review` instead of `extracted`, so a human can confirm the card.
REVIEW_CONFIDENCE = 0.5

# Long documents are extracted in page windows so the whole document is seen, not
# just its head. `_CHUNK_CHAR_BUDGET` packs whole pages into a chunk up to this
# many characters (one LLM call per chunk); a doc under the budget stays a single
# call. `_MAX_OCR_CHARS` is a hard per-call safety ceiling (e.g. one giant page).
_CHUNK_CHAR_BUDGET = 14_000
_MAX_OCR_CHARS = 50_000

# Minimum token overlap (fraction of fact tokens found in a block) before we
# trust an OCR block's bbox as a fact's provenance.
_BBOX_MIN_OVERLAP = 0.5

_VALUE_TYPES = frozenset({"money", "number", "date", "id", "string"})
_DATE_ROLES = frozenset({"issued", "due", "expiry", "event", "mentioned"})


# --- parsed result shapes (lenient validation) -----------------------------
def _coerce_date(value: object) -> dt.date | None:
    """Parse an ISO date string into a `date`; anything else becomes None."""
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


class ClassPrediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    confidence: float | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float | None:
        if v is None:
            return None
        try:
            return max(0.0, min(1.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None


class EntityGroups(BaseModel):
    model_config = ConfigDict(extra="ignore")

    people: list[str] = []
    organizations: list[str] = []
    places: list[str] = []


class DatePrediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: dt.date | None = None
    raw_text: str | None = None
    role: str = "mentioned"

    @field_validator("value", mode="before")
    @classmethod
    def _parse_value(cls, v: object) -> dt.date | None:
        return _coerce_date(v)

    @field_validator("role", mode="before")
    @classmethod
    def _valid_role(cls, v: object) -> str:
        return v if v in _DATE_ROLES else "mentioned"


class TypedFactPrediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    value: str | None = None
    value_numeric: float | None = None
    value_type: str = "string"
    unit: str | None = None
    page: int | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _stringify(cls, v: object) -> str | None:
        return None if v is None else str(v)

    @field_validator("value_numeric", mode="before")
    @classmethod
    def _numeric(cls, v: object) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @field_validator("value_type", mode="before")
    @classmethod
    def _valid_type(cls, v: object) -> str:
        return v if v in _VALUE_TYPES else "string"


class AtomicFactPrediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    page: int | None = None


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    summary: str | None = None
    classes: list[ClassPrediction] = []
    entities: EntityGroups = EntityGroups()
    dates: list[DatePrediction] = []
    typed_facts: list[TypedFactPrediction] = []
    atomic_facts: list[AtomicFactPrediction] = []


# --- prompt ----------------------------------------------------------------
_SYSTEM_PROMPT = """You are a meticulous document archivist. Read the OCR text \
of a single document and return ONLY a JSON object describing it. Do not invent \
facts; extract only what the text supports.

Return this exact shape:
{
  "title": "short human title for the document",
  "summary": "1-3 sentence neutral summary",
  "classes": [{"slug": "<one of the provided class slugs>", "confidence": 0.0-1.0}],
  "entities": {"people": [], "organizations": [], "places": []},
  "dates": [{"value": "YYYY-MM-DD", "raw_text": "as written", "role": "issued|due|expiry|event|mentioned"}],
  "typed_facts": [{"label": "snake_case_label", "value": "string", "value_numeric": number_or_null, "value_type": "money|number|date|id|string", "unit": "USD|kg|...|null", "page": 1}],
  "atomic_facts": [{"text": "a self-contained sentence stating one fact", "page": 1}]
}

Rules:
- The text is divided by "===== PAGE n =====" markers. Set the `page` of every fact/date to the page number of the marker it appears under. You may be given only a slice of a longer document — extract only from the pages shown here.
- Use ONLY class slugs from the provided catalog; omit classes that do not apply. Multi-label is allowed.
- Atomic facts must each stand alone (resolve pronouns; name the subject) and carry the page they came from.
- Put every quantity (amounts, totals, counts, IDs) into typed_facts with value_numeric set when numeric.
- Use null for unknown fields. Output JSON only — no prose, no code fences."""


def _format_catalog(classes: list[Class]) -> str:
    return "\n".join(f"- {c.slug}: {c.description or c.name}" for c in classes)


def build_messages(ocr_text: str, classes: list[Class]) -> list[dict]:
    """Assemble the chat messages for the extraction call (pure)."""
    text = ocr_text[:_MAX_OCR_CHARS]
    truncated = "\n\n[... text truncated ...]" if len(ocr_text) > _MAX_OCR_CHARS else ""
    user = (
        f"Class catalog (slug: description):\n{_format_catalog(classes)}\n\n"
        f"--- DOCUMENT OCR TEXT ---\n{text}{truncated}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# --- LLM call (the only network seam; monkeypatched in tests) ---------------
_client = None


def _deepseek_client():
    global _client
    if _client is None:
        from openai import OpenAI

        settings = get_settings()
        _client = OpenAI(
            api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url
        )
    return _client


def call_extraction_model(ocr_text: str, classes: list[Class]) -> tuple[str, str]:
    """Run the structured-output pass. Returns ``(raw_json, model_name)``."""
    settings = get_settings()
    response = _deepseek_client().chat.completions.create(
        model=settings.deepseek_model,
        messages=build_messages(ocr_text, classes),
        response_format={"type": "json_object"},
        temperature=0,
    )
    return response.choices[0].message.content or "{}", settings.deepseek_model


def parse_extraction(content: str) -> ExtractionResult:
    """Parse a raw model response into a validated `ExtractionResult` (lenient)."""
    cleaned = content.strip()
    if cleaned.startswith("```"):  # defensive: strip accidental code fences
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    return ExtractionResult.model_validate(data)


# --- page-window chunking (long documents) ---------------------------------
@dataclass
class PageChunk:
    """A contiguous run of pages extracted in a single LLM call.

    `text` carries `===== PAGE n =====` markers so the model can attribute each
    fact to its real page number, even within a multi-page chunk.
    """

    start_page: int
    end_page: int
    text: str


def _page_marker(page: int) -> str:
    return f"\n\n===== PAGE {page} =====\n"


def chunk_pages(
    pages: list[ocr.OcrPage], budget: int = _CHUNK_CHAR_BUDGET
) -> list[PageChunk]:
    """Pack whole pages into chunks of up to `budget` characters (pure).

    A page is never split across chunks; a single page larger than the budget
    becomes its own (over-budget) chunk. Returns one chunk per call the extractor
    will make — a short document yields exactly one.
    """
    chunks: list[PageChunk] = []
    buf: list[str] = []
    start: int | None = None
    last = 0
    size = 0
    for page in pages:
        segment = _page_marker(page.page) + (page.text or "")
        if buf and size + len(segment) > budget:
            chunks.append(PageChunk(start, last, "".join(buf).strip()))
            buf, size, start = [], 0, None
        if start is None:
            start = page.page
        buf.append(segment)
        size += len(segment)
        last = page.page
    if buf:
        chunks.append(PageChunk(start, last, "".join(buf).strip()))
    return chunks


# --- merge per-chunk cards into one ----------------------------------------
def _dedup_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def merge_results(results: list[ExtractionResult]) -> ExtractionResult:
    """Combine per-chunk extractions into one card (pure).

    Title/summary take the first chunk that supplies them (the document opening);
    classes keep the highest confidence per slug; entities/dates/typed/atomic
    facts are unioned with duplicates removed. A single result passes through
    unchanged, so short (one-chunk) documents are unaffected.
    """
    if len(results) == 1:
        return results[0]

    title = next((r.title for r in results if r.title), None)
    summary = next((r.summary for r in results if r.summary), None)

    classes: dict[str, ClassPrediction] = {}
    for r in results:
        for c in r.classes:
            current = classes.get(c.slug)
            if current is None or (c.confidence or 0.0) > (current.confidence or 0.0):
                classes[c.slug] = c

    entities = EntityGroups(
        people=_dedup_names([n for r in results for n in r.entities.people]),
        organizations=_dedup_names([n for r in results for n in r.entities.organizations]),
        places=_dedup_names([n for r in results for n in r.entities.places]),
    )

    dates: list[DatePrediction] = []
    seen_dates: set[tuple] = set()
    for r in results:
        for d in r.dates:
            key = (d.value, d.role, d.raw_text)
            if key not in seen_dates:
                seen_dates.add(key)
                dates.append(d)

    typed_facts: list[TypedFactPrediction] = []
    seen_typed: set[tuple] = set()
    for r in results:
        for f in r.typed_facts:
            key = (f.label, f.value, f.page)
            if key not in seen_typed:
                seen_typed.add(key)
                typed_facts.append(f)

    atomic_facts: list[AtomicFactPrediction] = []
    seen_atomic: set[str] = set()
    for r in results:
        for f in r.atomic_facts:
            key = f.text.strip().lower()
            if key and key not in seen_atomic:
                seen_atomic.add(key)
                atomic_facts.append(f)

    return ExtractionResult(
        title=title,
        summary=summary,
        classes=list(classes.values()),
        entities=entities,
        dates=dates,
        typed_facts=typed_facts,
        atomic_facts=atomic_facts,
    )


# --- card fan-out ----------------------------------------------------------
def _load_class_catalog(db: Session, account_id: uuid.UUID) -> list[Class]:
    return list(
        db.scalars(select(Class).where(Class.account_id == account_id)).all()
    )


def _clear_previous_extraction(
    db: Session, account_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """Idempotency: drop a document's prior card so a re-run is a clean rewrite.

    Shared `entities` rows are left in place (other documents may reference
    them); only the per-document join/fact rows are removed.
    """
    for model in (DocumentClass, DocumentEntity, DocumentDate, TypedFact, DocumentFact):
        db.execute(
            delete(model).where(
                model.account_id == account_id, model.document_id == document_id
            )
        )


def _upsert_entity(
    db: Session, account_id: uuid.UUID, name: str, type_: str
) -> Entity | None:
    normalized = name.strip().lower()
    if not normalized:
        return None
    entity = db.scalar(
        select(Entity).where(
            Entity.account_id == account_id,
            Entity.type == type_,
            Entity.normalized_name == normalized,
        )
    )
    if entity is None:
        entity = Entity(
            account_id=account_id,
            name=name.strip(),
            normalized_name=normalized,
            type=type_,
        )
        db.add(entity)
        db.flush()
    return entity


def _write_card(
    db: Session, document: Document, result: ExtractionResult, catalog: list[Class]
) -> None:
    account_id = document.account_id
    slug_to_id = {c.slug: c.id for c in catalog}

    for pred in result.classes:
        class_id = slug_to_id.get(pred.slug)
        if class_id is None:  # model invented a slug — ignore it
            continue
        db.add(
            DocumentClass(
                account_id=account_id,
                document_id=document.id,
                class_id=class_id,
                confidence=pred.confidence,
                assigned_by="model",
            )
        )

    groups = (
        ("person", result.entities.people),
        ("organization", result.entities.organizations),
        ("place", result.entities.places),
    )
    for type_, names in groups:
        seen: set[str] = set()
        for name in names:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            entity = _upsert_entity(db, account_id, name, type_)
            if entity is not None:
                db.add(
                    DocumentEntity(
                        account_id=account_id,
                        document_id=document.id,
                        entity_id=entity.id,
                    )
                )

    for date in result.dates:
        db.add(
            DocumentDate(
                account_id=account_id,
                document_id=document.id,
                value=date.value,
                raw_text=date.raw_text,
                role=date.role,
            )
        )

    for fact in result.typed_facts:
        db.add(
            TypedFact(
                account_id=account_id,
                document_id=document.id,
                label=fact.label,
                value=fact.value,
                value_numeric=fact.value_numeric,
                value_type=fact.value_type,
                unit=fact.unit,
                page=fact.page,
            )
        )


def _bbox_for_fact(
    cached: ocr.OcrResult | None, page: int | None, text: str
) -> dict | None:
    """Best-effort provenance: the OCR block on `page` that most overlaps `text`.

    Returns a `{"page", "bbox"}` dict when a confident match is found, else None.
    Atomic facts are model-paraphrased, so an exact match is not expected; we use
    token overlap and only attach a bbox when it clears `_BBOX_MIN_OVERLAP`.
    """
    if cached is None or page is None:
        return None
    fact_tokens = {t for t in text.lower().split() if len(t) > 2}
    if not fact_tokens:
        return None
    ocr_page = next((p for p in cached.pages if p.page == page), None)
    if ocr_page is None:
        return None

    best_overlap = 0.0
    best_bbox = None
    for block in ocr_page.blocks:
        block_tokens = {t for t in block.text.lower().split() if len(t) > 2}
        if not block_tokens:
            continue
        overlap = len(fact_tokens & block_tokens) / len(fact_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_bbox = block.bbox
    if best_bbox is None or best_overlap < _BBOX_MIN_OVERLAP:
        return None
    return {"page": page, "bbox": best_bbox}


def _write_atomic_facts(
    db: Session,
    document: Document,
    result: ExtractionResult,
    cached: ocr.OcrResult | None,
) -> int:
    count = 0
    for fact in result.atomic_facts:
        text = fact.text.strip()
        if not text:
            continue
        db.add(
            DocumentFact(
                account_id=document.account_id,
                document_id=document.id,
                text=text,
                page=fact.page,
                bbox=_bbox_for_fact(cached, fact.page, text),
                # `embedding` stays null until Phase 4.
            )
        )
        count += 1
    return count


def _route_status(result: ExtractionResult) -> str:
    """`extracted` for a confident card, else `needs_review`."""
    if not result.classes:
        return "needs_review"
    top = max((c.confidence or 0.0) for c in result.classes)
    return "extracted" if top >= REVIEW_CONFIDENCE else "needs_review"


# --- orchestration (background entry point) --------------------------------
# Statuses we are allowed to (re-)extract from. `extracted`/`needs_review` are
# included so a manual re-run is possible without resetting the document first.
_EXTRACTABLE = frozenset({"ocr_done", "extracted", "needs_review"})


def run_extraction(document_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Extract one document's card + atomic facts and advance its status.

    Runs after OCR (chained or invoked directly). Opens its own session, never
    crosses account scope, and swallows its own failures (marking the document
    `failed`) so it is safe to call from a background task.
    """
    started = time.monotonic()
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None or document.account_id != account_id:
            return  # deleted or wrong account — never cross-scope
        if document.status not in _EXTRACTABLE or not document.ocr_text:
            return  # nothing to extract (no OCR text, or wrong stage)

        record_event(
            db, account_id=account_id, document_id=document_id,
            stage="extraction", status="started",
        )
        db.commit()

        try:
            catalog = _load_class_catalog(db, account_id)

            # Split into page windows so the whole document is seen, not just its
            # head. The OCR cache holds per-page text; without it, fall back to a
            # single chunk over the stored ocr_text.
            cached = ocr.load_cached_ocr(document.file_hash)
            if cached and cached.pages:
                chunks = chunk_pages(cached.pages, _CHUNK_CHAR_BUDGET)
            else:
                chunks = [PageChunk(1, document.page_count or 1, document.ocr_text)]

            raw_chunks: list[dict] = []
            results: list[ExtractionResult] = []
            model_name = ""
            for chunk in chunks:
                raw, model_name = call_extraction_model(chunk.text, catalog)
                results.append(parse_extraction(raw))
                try:
                    raw_chunks.append(json.loads(raw))
                except json.JSONDecodeError:
                    raw_chunks.append({"raw": raw})
            result = merge_results(results)

            _clear_previous_extraction(db, account_id, document_id)
            _write_card(db, document, result, catalog)
            fact_count = _write_atomic_facts(db, document, result, cached)

            document.extraction_raw = {"chunk_count": len(chunks), "chunks": raw_chunks}
            document.extraction_model = model_name
            if result.title:
                document.title = result.title
            document.summary = result.summary
            document.status = _route_status(result)

            record_event(
                db, account_id=account_id, document_id=document_id,
                stage="extraction", status="succeeded",
                duration_ms=int((time.monotonic() - started) * 1000),
                detail={
                    "model": model_name,
                    "status": document.status,
                    "chunks": len(chunks),
                    "classes": len(result.classes),
                    "entities": (
                        len(result.entities.people)
                        + len(result.entities.organizations)
                        + len(result.entities.places)
                    ),
                    "dates": len(result.dates),
                    "typed_facts": len(result.typed_facts),
                    "atomic_facts": fact_count,
                },
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — record any failure, don't crash the worker
            db.rollback()
            document = db.get(Document, document_id)
            if document is not None:
                document.status = "failed"
                document.error = f"Extraction failed: {exc}"
            record_event(
                db, account_id=account_id, document_id=document_id,
                stage="extraction", status="failed",
                error=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            db.commit()
