"""Pydantic response models for the API.

These mirror `API_CONTRACTS.md`. The full `DocumentCard` (classes, entities,
typed facts) is filled out in the extraction phase; for now `DocumentOut` carries
the fields populated by ingest + OCR.
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
