"""SQLAlchemy ORM models mapped to the **existing** filemindr schema.

The schema is owned by `schema.sql` / Alembic `0001` — these classes only map
onto tables that already exist. Do not call `Base.metadata.create_all()`; tables
and enum types are created by migrations, never by the ORM.

Coverage is the document-core (identity, tenancy, classes, documents, card, and
atomic facts). Chat, observability, and billing tables are mapped in their own
phases when first needed.

Every account-scoped table carries `account_id`; the scoping layer
(`app.core.scoping`) relies on that attribute being present.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    REAL,
    Text,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# --- Postgres ENUM types (already created by the migration) ---------------
# `create_type=False` keeps the ORM from ever trying to emit `CREATE TYPE`.
def _pg_enum(*labels: str, name: str) -> ENUM:
    return ENUM(*labels, name=name, create_type=False)


account_type_enum = _pg_enum("personal", "company", name="account_type")
member_role_enum = _pg_enum("member", "admin", "owner", name="member_role")
document_source_enum = _pg_enum("web_upload", "email_in", name="document_source")
document_status_enum = _pg_enum(
    "received", "ocr_done", "extracted", "indexed", "failed", "needs_review",
    name="document_status",
)
ocr_engine_enum = _pg_enum("pdf_text_layer", "google_vision", "docx", name="ocr_engine")
assigned_by_enum = _pg_enum("model", "user", name="assigned_by")
entity_type_enum = _pg_enum("person", "organization", "place", name="entity_type")
date_role_enum = _pg_enum(
    "issued", "due", "expiry", "event", "mentioned", name="date_role"
)
value_type_enum = _pg_enum(
    "money", "number", "date", "id", "string", name="value_type"
)
event_stage_enum = _pg_enum(
    "received", "ocr", "extraction", "embedding", "indexing", name="event_stage"
)
event_status_enum = _pg_enum("started", "succeeded", "failed", name="event_status")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )


# --- identity & tenancy ----------------------------------------------------
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(account_type_enum, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))

    members: Mapped[list["AccountMember"]] = relationship(
        back_populates="account", passive_deletes=True
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))

    memberships: Mapped[list["AccountMember"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class AccountMember(Base):
    __tablename__ = "account_members"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(member_role_enum, nullable=False, server_default=sql_text("'member'"))
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))

    account: Mapped[Account] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


# --- documents & extracted card -------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(document_source_enum, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_long: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        document_status_enum, nullable=False, server_default=sql_text("'received'")
    )
    error: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_engine: Mapped[str | None] = mapped_column(ocr_engine_enum)
    extraction_raw: Mapped[dict | None] = mapped_column(JSONB)
    extraction_model: Mapped[str | None] = mapped_column(Text)
    summary_embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


class DocumentClass(Base):
    __tablename__ = "document_classes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(REAL)
    assigned_by: Mapped[str] = mapped_column(
        assigned_by_enum, nullable=False, server_default=sql_text("'model'")
    )
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(entity_type_enum, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


class DocumentEntity(Base):
    __tablename__ = "document_entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))


class DocumentDate(Base):
    __tablename__ = "document_dates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[dt.date | None] = mapped_column(Date)
    raw_text: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(date_role_enum, nullable=False, server_default=sql_text("'mentioned'"))
    page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


class TypedFact(Base):
    __tablename__ = "typed_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[float | None] = mapped_column(Numeric)
    value_type: Mapped[str] = mapped_column(value_type_enum, nullable=False, server_default=sql_text("'string'"))
    unit: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


# --- atomic facts (primary retrieval unit) --------------------------------
class DocumentFact(Base):
    __tablename__ = "document_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    # `fts` is a generated tsvector column owned by Postgres — read-only here.
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))


# --- pipeline observability (append-only) ---------------------------------
class ProcessingEvent(Base):
    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(event_stage_enum, nullable=False)
    status: Mapped[str] = mapped_column(event_status_enum, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False, server_default=sql_text("now()"))
