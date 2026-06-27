"""Pydantic response models for the API.

These mirror `API_CONTRACTS.md`. `DocumentOut` is the light list/ingest view;
`DocumentCardOut` adds the extracted card (classes, entities, dates, typed facts)
returned by the document-detail endpoint once extraction has run.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    source: str
    original_filename: str
    mime_type: str | None
    byte_size: int | None
    title: str | None
    summary: str | None
    language: str | None
    page_count: int | None
    created_at: dt.datetime


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


# --- document card (detail view) -------------------------------------------
class ClassCardOut(BaseModel):
    slug: str
    name: str | None = None
    confidence: float | None = None


class EntitiesCardOut(BaseModel):
    people: list[str] = []
    organizations: list[str] = []
    places: list[str] = []


class DateCardOut(BaseModel):
    value: dt.date | None = None
    raw_text: str | None = None
    role: str


class TypedFactCardOut(BaseModel):
    label: str
    value: str | None = None
    value_numeric: float | None = None
    type: str
    unit: str | None = None
    page: int | None = None


class DocumentCardOut(DocumentOut):
    classes: list[ClassCardOut] = []
    entities: EntitiesCardOut = EntitiesCardOut()
    dates: list[DateCardOut] = []
    typed_facts: list[TypedFactCardOut] = []
    fact_count: int = 0
