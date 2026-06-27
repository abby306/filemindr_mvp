"""Raw file persistence for ingested documents.

Files are content-addressed: the SHA-256 of the bytes is both the dedup key and
the on-disk name, laid out as ``<storage_root>/<account_id>/<hash><ext>``. Writes
are atomic (temp file then ``os.replace``) so a crashed upload never leaves a
half-written file that looks complete.

`get_storage_root` is the single point that resolves the storage directory, so
tests can redirect it without touching settings.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


@dataclass(frozen=True)
class StoredFile:
    file_hash: str
    storage_path: str
    byte_size: int


def get_storage_root() -> Path:
    """Absolute root directory for raw files (override target for tests)."""
    return get_settings().storage_path


def compute_hash(content: bytes) -> str:
    """SHA-256 hex digest of `content` — the content-address / dedup key."""
    return hashlib.sha256(content).hexdigest()


def save_upload(
    content: bytes,
    account_id: uuid.UUID,
    ext: str,
    *,
    storage_root: Path | None = None,
) -> StoredFile:
    """Persist `content` for `account_id` and return its address.

    Idempotent by construction: the same bytes map to the same path, so a repeat
    upload simply overwrites with identical content. `ext` should include the
    leading dot (e.g. ``.pdf``); it is only cosmetic for the filename.
    """
    root = storage_root or get_storage_root()
    file_hash = compute_hash(content)
    account_dir = root / str(account_id)
    account_dir.mkdir(parents=True, exist_ok=True)

    final_path = account_dir / f"{file_hash}{ext}"
    tmp_path = account_dir / f".{file_hash}{ext}.{uuid.uuid4().hex}.tmp"
    tmp_path.write_bytes(content)
    os.replace(tmp_path, final_path)

    return StoredFile(
        file_hash=file_hash,
        storage_path=str(final_path),
        byte_size=len(content),
    )
