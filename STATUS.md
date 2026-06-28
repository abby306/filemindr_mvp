# STATUS.md — Development State

> **Purpose:** a self-contained handoff for the next agent/session. Read this before touching code so you don't have to re-traverse the whole repo. Update it at the end of each development cycle.
>
> **Last updated:** 2026-06-28 · after **Phase 4 + pre-Phase-5 cleanup pass** (resilience, re-drive, concurrency, streaming upload, native-PDF bboxes, eval scaffold) · branch `main` (commits pending) · prior push at `dc6e73e` (`github.com/abby306/filemindr_mvp`).

---

## 1. What Filemindr is (one paragraph)

An intelligent document archivist. Users upload PDFs / images / Word docs (web upload now; email-in later). Each document is OCR'd if needed, then turned **once** at ingest into a structured **document card** (type/class, summary, typed facts, people, dates) + **atomic facts** (self-contained sentences with page/bbox provenance). A chat agent does grounded retrieval over those structured facts with citations. Core principle: **retrieve against structured data, not raw text** — keeps query context small and precise. Everything is **scoped by `account_id`** (one personal + one company account; no RBAC yet).

Authoritative design docs live in [`agent_guide/`](agent_guide/): `PRD.md` → `ARCHITECTURE.md` → `TECH_SPEC.md`, plus `API_CONTRACTS.md`, `AGENTS.md` (rules for agents), `CODING_STANDARDS.md` (conventions), `setup.md` (machine/env).

---

## 2. Current status at a glance

| Phase | State | Notes |
|---|---|---|
| 1 — FastAPI foundations | ✅ Done | config, ORM, session, auth, scoping, `/health`, seed |
| 2 — Ingest + OCR routing | ✅ Done | upload, dedup, storage, OCR pipeline → `ocr_done` |
| 3 — Extraction | ✅ Done | DeepSeek card + atomic facts → `extracted`/`needs_review`; card API |
| 4 — Embeddings/index | ✅ Done | bge-base embeds facts+summary → `indexed`; both HNSW stages indexed |
| 5 — Retrieval + synthesis | ⏭️ **Next** | intent router, hybrid retrieval, grounded answers |
| 6 — Frontend (Next.js) | ⏭️ Pending | Upload / Document view / Ask / Ratings |
| 7 — Analytics + billing | ⏭️ Pending | usage counters, plans, quotas |

- **Tests:** 101 passing (`pytest -q`). Run against the **live local Postgres**. Offline — Vision, DeepSeek, and the bge encoder are all mocked.
- **Document pipeline status flow:** `received → ocr_done → extracted → indexed` (+ `failed` / `needs_review`). The full chain auto-runs on upload: OCR → extraction → embedding. **Every successfully-extracted doc is embedded** (so it is retrievable); confident docs reach **`indexed`**, low-confidence ones are embedded but **stay `needs_review`** (searchable + flagged for human review). Stuck/`failed` docs can be re-driven idempotently (`scripts/reprocess.py`).
- **Resilience:** transient DeepSeek/Vision failures are retried (bounded backoff); a single failing chunk/page is skipped + recorded rather than failing the whole doc (only all-fail → `failed`). Per-chunk extraction and per-page OCR run with bounded concurrency. Uploads stream to disk with a size cap.
- **DB:** 22 tables + `v_document_pipeline` view; migration `0002` adds the `documents.summary_embedding` HNSW index (both vector stages now indexed — see §6). Seed: 1 dev user, personal + company accounts, 14 system classes each, **0 documents**.

---

## 3. Environment & how to run

- **Machine:** Ubuntu, Python 3.12 (pyenv), venv at `.venv`. **Native services, no Docker this phase.**
- **Postgres 16 + pgvector** (`vector` 0.8.3) at `postgresql+psycopg://filemindr:localdev@localhost:5432/filemindr`.
- **Redis** at `localhost:6379` — installed but **not used yet** (background work currently uses FastAPI `BackgroundTasks`, not a Redis worker).

```bash
source .venv/bin/activate
python -m scripts.seed            # idempotent: dev user, 2 accounts, default classes
uvicorn app.main:app --reload     # serves on :8000
pytest -q                         # 37 tests
```

**Secrets** (in `.env`, git-ignored — all four set & verified working): `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS` (→ `secrets/vision-credentials.json`). `.env.example` lists the keys without values. **Never commit secrets**; `.gitignore` covers `.env`, `secrets/`, `storage/`, `*credentials*.json`, `*-key.json`.

**Dev auth (how to call protected endpoints):** the bearer token *is* the user's UUID.
```
Authorization: Bearer 39a05457-5daa-4236-a191-2c6a78223563   # dev user abdullahasad70@gmail.com
X-Account-Id:  86f4f4bf-a499-470a-a0ce-3b303601ca53          # personal account
#              08874a69-6628-4d5d-9412-3ec488df8c90          # company account "Acme Inc"
```
(The user is a member of both accounts, so `X-Account-Id` is required to disambiguate.)

---

## 4. File-by-file map (`app/`, `scripts/`, `tests/`)

### `app/core/` — config, auth, tenancy
- **`config.py`** — `Settings(BaseSettings)` from `.env`; `get_settings()` is `lru_cache`d. Fields: `database_url`, `redis_url`, `openai_api_key`, `deepseek_api_key`, `gemini_api_key`, `google_application_credentials`, `deepseek_base_url`, `deepseek_model`, `retry_max_attempts` (3), `retry_base_delay` (0.5), `max_parallel_calls` (4), `max_upload_mb` (50), `storage_dir`, `app_env`. `.storage_path` → absolute `Path`.
- **`retry.py`** — `with_retry(fn, *, attempts, base_delay, is_retryable, max_delay, sleep?, rng?)`: bounded retries, exponential backoff + full jitter; retries only when the injected `is_retryable(exc)` is true (provider predicates live in `ocr`/`extraction`). `sleep`/`rng` injectable for instant deterministic tests.
- **`concurrency.py`** — `map_bounded(func, items, *, max_workers)`: order-preserving `ThreadPoolExecutor` map with a hard worker cap; serial fast-path for empty/single inputs. Used to parallelize **only** the network phase (per-chunk extraction, per-page OCR); DB writes stay serial.
- **`auth.py`** — `get_current_user` dependency. Parses `Authorization: Bearer <uuid>`, looks up `User`, requires `is_active`. Raises 401 otherwise. **This is the single seam to swap in real auth (JWT/session) later.**
- **`scoping.py`** — the tenancy boundary. `AccountScope(db, user, account)` with `.select(Model)` / `.query(Model)` that **auto-apply `WHERE account_id = :active`** and **raise `ValueError` if the model has no `account_id`** (so an unscoped query is a caught bug, not a silent leak). `.owns(obj)` helper. `get_current_account` dependency: resolves active account from `X-Account-Id` header (or sole membership), **verifies membership** (403 if not a member), returns the `AccountScope`. **Every account-scoped query in the app must go through this.**
- **`default_classes.py`** — `DEFAULT_CLASSES`: 14 `ClassSeed(slug, name, description)` rows (invoice, receipt, contract, id_document, bank_statement, tax_document, payslip, utility_bill, insurance, medical_record, report, letter, resume, warranty). Descriptions feed the future classifier.

### `app/db/` — persistence
- **`session.py`** — SQLAlchemy `engine` (psycopg, `pool_pre_ping`), `SessionLocal` sessionmaker, `get_db()` FastAPI dependency (yields + closes).
- **`models.py`** — ORM mapped to the **existing** schema. **Never `create_all`** — Alembic owns DDL. `Base = DeclarativeBase`. Postgres enums via `_pg_enum(..., create_type=False)`. **Mapped tables:** `Account`, `User`, `AccountMember`, `Class`, `Document`, `DocumentClass`, `Entity`, `DocumentEntity`, `DocumentDate`, `TypedFact`, `DocumentFact` (has `embedding Vector(768)`; `fts` tsvector is DB-generated, not mapped), `ProcessingEvent` (bigserial PK; append-only pipeline log). **Not yet mapped** (add when their phase arrives): `conversations`, `messages`, `retrieval_traces`, `answer_ratings`, `usage_events`, `plans`, `subscriptions`, `invoices`, `usage_counters`. Membership relationships use `passive_deletes=True` (defer to DB `ON DELETE CASCADE`).

### `app/services/` — business logic
- **`storage.py`** — raw file persistence. `compute_hash` (SHA-256); **`save_stream(stream, account_id, ext, *, max_bytes, storage_root?) → StoredFile`** streams a chunk-readable upload to a temp file, hashing **incrementally** (never buffering the whole body), raises **`FileTooLargeError`** the moment `max_bytes` is exceeded (temp cleaned up), then atomic-renames to the content-addressed path `STORAGE_DIR/<account_id>/<hash><ext>`. `get_storage_root()` is the single settings read (monkeypatched in tests).
- **`events.py`** — `record_event(db, account_id, document_id, stage, status, detail?, error?, duration_ms?)` → appends a `ProcessingEvent`. Caller controls commit.
- **`reprocessing.py`** — re-drive stuck/`failed` docs. `reprocess_document(doc_id, account_id)` routes by status to the right idempotent entry point (`received`/`failed`→`run_ocr`, `ocr_done`→`run_extraction`, `extracted`/`needs_review`→`run_embedding`; `indexed` terminal); clears `error` on a `failed` doc first. `reprocess_stuck(*, account_id?, statuses?)` sweeps non-terminal/`failed` docs (default `received`/`ocr_done`/`extracted`/`failed`; `needs_review` excluded — already embedded). CLI: `python -m scripts.reprocess`.
- **`ocr.py`** — OCR routing brain (~360 lines). Key pieces:
  - MIME constants: `PDF_MIME`, `DOCX_MIME`, `IMAGE_MIMES`, `ALLOWED_MIME_TYPES`; `extension_for(mime, filename)`.
  - `choose_engine(mime_type, has_text_layer) → "pdf_text_layer"|"google_vision"|"docx"` — **pure, unit-tested**.
  - `probe_pdf_text_layer(path)` — PyMuPDF; returns `(page_texts, page_count, has_usable_layer)`. Thresholds: ≥100 total chars **and** ≥20 chars/page.
  - `extract_docx(path)` (python-docx); `ocr_image_via_vision(path)`; `ocr_pdf_via_vision(path)` (rasterizes each page at 200 DPI via fitz → Vision). **Both the Vision path *and* the native PDF text-layer path now populate `OcrPage.blocks`** with 4-vertex bbox polygons (text-layer via PyMuPDF `get_text("blocks")`), so `_bbox_for_fact` gives box-accurate provenance for every input type.
  - **Resilience:** Vision calls go through `_vision_ocr_with_retry` (`with_retry` + `_is_transient_vision` predicate: 429/500/503/504/timeout). `ocr_pdf_via_vision` rasterizes serially (PyMuPDF isn't thread-safe), OCRs pages **in parallel** (`map_bounded`, cap `max_parallel_calls`), and is **partial-tolerant**: a page that keeps failing is recorded in `OcrResult.failed_pages` (round-trips through the cache) with empty text; only an all-page failure raises.
  - `detect_language(text)` (langdetect, fixed seed; Vision locale preferred when available).
  - **OCR cache** keyed by file hash: `STORAGE_DIR/ocr_cache/<hash>.json` (`load_cached_ocr`/`save_cached_ocr`) — identical bytes never re-OCR'd, even across accounts (it's a pure function of content).
  - `ocr_document(mime_type, storage_path) → OcrResult` (no DB) and **`run_ocr(document_id, account_id)`** — the background entry point: opens its own `SessionLocal`, logs `ocr started`, routes (or cache-hit), writes `ocr_text`/`ocr_engine`/`page_count`/`language`, sets status `ocr_done`, logs `ocr succeeded` (or on exception sets `failed` + logs `ocr failed`).
  - Dataclasses: `OcrBlock(text, bbox)`, `OcrPage(page, text, blocks)`, `OcrResult(engine, page_count, language, text, pages)` with `to_cache`/`from_cache`.
  - **On `ocr_done` success, `run_ocr` chains `extraction.run_extraction(document_id, account_id)`** (local import to avoid a cycle; extraction opens its own session and swallows its own failures, so the committed OCR result is never disturbed).
- **`extraction.py`** — Phase-3 extraction brain. Key pieces:
  - `parse_extraction(raw) → ExtractionResult` — **pure, lenient, unit-tested**: clamps confidence to [0,1], coerces `value_numeric`, bad enums → defaults (`value_type`→`string`, date `role`→`mentioned`), unparseable dates → null, strips stray code fences. Pydantic sub-models: `ClassPrediction`, `EntityGroups`, `DatePrediction`, `TypedFactPrediction`, `AtomicFactPrediction`.
  - **Page-window chunking (long docs):** `chunk_pages(pages, budget=14k) → [PageChunk]` packs whole pages (never split) into chunks marked with `===== PAGE n =====`; `merge_results([ExtractionResult]) → ExtractionResult` unions the per-chunk cards (classes keep max confidence/slug; entities/dates/typed/atomic facts deduped; title/summary = first non-empty). Both **pure + unit-tested**. A doc under budget is a single chunk (one call) — unchanged behavior; only long docs fan out. This fixed multi-page coverage (the 20-page sample went from facts truncated at page 10 → facts across all 20 pages; the 5-page NDA from 1/5 → 5/5 pages covered).
  - `build_messages(text, classes)` — pure; hard safety ceiling 50k chars/call; injects the account's class catalog (slug: description) and instructs the model to attribute each fact to its `===== PAGE n =====` marker.
  - **`call_extraction_model(text, classes) → (raw_json, model_name)`** — the **only network seam** (DeepSeek via `OpenAI(base_url=…)`, `response_format=json_object`, temp 0). Wrapped in `with_retry` (`_is_transient_llm` predicate); chunks are extracted **in parallel** (`map_bounded`, cap `max_parallel_calls`) with results kept in chunk order. **Partial-tolerant:** a chunk that fails after retries is recorded in `extraction_raw.failed_chunks` + event detail and skipped; only an all-chunk failure → `failed`. Monkeypatched in tests.
  - Fan-out writers (all account-scoped): `_write_card` (classes→`document_classes` matching catalog slugs, **unknown slugs dropped**; entities upserted into `entities` by `(account, type, normalized_name)` then linked via `document_entities`, deduped per doc; dates→`document_dates`; typed facts→`typed_facts`), `_write_atomic_facts` (→`document_facts`, **best-effort bbox** via token-overlap match against OCR-cache blocks on the fact's page; `embedding` stays null until Phase 4).
  - `_route_status` → `extracted` if top-class confidence ≥ `REVIEW_CONFIDENCE` (0.5), else `needs_review` (also `needs_review` when no class predicted).
  - **`run_extraction(document_id, account_id)`** — background entry point: own session, account-scoped, **idempotent** (clears the prior card before rewriting, so re-runs are clean). Loads the OCR-cache artifact → `chunk_pages` → one `call_extraction_model` per chunk → `merge_results` → fan-out (no cache ⇒ single-chunk fallback over `ocr_text`). Saves `extraction_raw` (`{chunk_count, chunks:[parsed JSON per chunk]}`), `extraction_model`, `title`, `summary`; logs `processing_events(extraction, …)` with the chunk count; on exception sets `failed`. Re-extractable from `ocr_done`/`extracted`/`needs_review`. **On `extracted` (not `needs_review`), chains `embeddings.run_embedding`** (local import).
- **`embeddings.py`** — Phase-4 local embeddings (**`BAAI/bge-base-en-v1.5`**, 768-d, CPU; lazy singleton). Key pieces:
  - **Asymmetric encoding** (bge convention, matters for retrieval accuracy): `embed_passages(texts)` for indexing (no prefix); `embed_query(q)` for Phase-5 search (prepends `QUERY_INSTRUCTION`). Both go through `_encode` (the single compute seam, **`normalize_embeddings=True`** so cosine distance is exact) — tests stub it, never downloading the model. The model is a **thread-safe** lazy singleton (`_get_model` uses double-checked locking around `_load_model`), so concurrent first uploads load it once.
  - **`run_embedding(document_id, account_id)`** — background entry point: own session, account-scoped, idempotent (overwrites vectors in place). Embeds all `document_facts.text` → `embedding` and `documents.summary` → `summary_embedding`; status → `indexed`. A `needs_review` doc is still embedded (searchable) but **keeps its review flag** rather than flipping to `indexed`. Logs `processing_events(embedding, …)`; on exception sets `failed`. Re-indexable from `extracted`/`indexed`/`needs_review`.

### `app/api/` — HTTP layer
- **`schemas.py`** — Pydantic response models. `DocumentOut` (id, status, source, original_filename, mime_type, byte_size, title, summary, language, page_count, created_at — `from_attributes=True`) is the light list/ingest view. `DocumentListOut` (items + next_cursor). **`DocumentCardOut`** (extends `DocumentOut`) adds `classes` (`ClassCardOut` slug/name/confidence), `entities` (`EntitiesCardOut` people/orgs/places), `dates` (`DateCardOut`), `typed_facts` (`TypedFactCardOut`; `value_type`→`type`), and `fact_count` — returned by the document-detail endpoint.
- **`documents.py`** — `APIRouter(prefix="/api/v1")`. Endpoints:
  - `POST /documents` — multipart upload. `_resolve_mime` (header → extension fallback); 415 if unsupported. **Streams** the upload via `save_stream` in a threadpool with `max_upload_mb` cap → **413** if over cap, **400** if empty. **Dedup** on `(account_id, file_hash)` (returns existing with **200**); else insert `Document` at `received`, `record_event(received, succeeded)`, commit, then **schedule `ocr.run_ocr` as a `BackgroundTask`**, return **201**.
  - `GET /documents` — account-scoped list, newest first, `status` filter, **keyset pagination** (opaque base64 cursor of `(created_at, id)`), `limit` 1–200.
  - `GET /documents/{id}` — account-scoped detail returning the **full `DocumentCardOut`** (`_build_card` assembles classes/entities/dates/typed_facts/`fact_count` via scoped queries); **404** for another account's doc. Card sections are empty until extraction runs.

### `app/main.py`
FastAPI app. `app.include_router(documents_router)`. `GET /health` (unauth; `SELECT 1` DB check → 200/503). `GET /api/v1/me` (auth+scoping demo, returns user+account). **Note:** FastAPI 0.138 represents included routers as a lazy `_IncludedRouter` in `app.routes`; verify routes via `app.openapi()["paths"]`, not by scanning `app.routes`.

### `scripts/`
- **`seed.py`** — idempotent `python -m scripts.seed`. Creates dev user (`abdullahasad70@gmail.com`), personal + company accounts, memberships (both `owner`), and the 14 system classes per account. Prints the dev-user UUID (bearer token) + account UUIDs.
- **`reprocess.py`** — `python -m scripts.reprocess [--statuses ..] [--account ..]`. Sweeps stuck/`failed` docs via `reprocessing.reprocess_stuck`.

### `eval/` — retrieval eval harness (built pre-Phase-5; see `eval/README.md`)
- **`schema.py`** — `GoldQuery`/`RetrievedAnswer`; `load_gold(path)` (YAML).
- **`scorers.py`** — pure `recall_at_k` (doc + fact-substring), `answer_correctness`, `score_dataset` (per-type + overall, `None`-aware means; `normalize()` is the LLM-judge seam).
- **`gold/seed.yaml`** — 8 illustrative queries across the 4 intents, grounded in the Phase-3/4 sample docs (`expected_doc_ids` are slugs → map to real UUIDs in a seeded eval corpus).
- **`run.py`** — `python -m eval.run [--k N] [--gold path]`; scores a `retrieve(query)` callable, ships a fixture stub. **Phase 5 wiring point** documented in the README.

### `tests/` (101 tests, all live-DB; every network/model seam mocked)
- **`conftest.py`** — `db` fixture (session, **rolls back**); `seeded_account` fixture (commits a throwaway user + personal/company accounts + memberships, yields their ids, cascade-deletes on teardown).
- **`test_config.py`** — settings load, caching, env overrides, defaults.
- **`test_db.py`** — engine connects, pgvector present, `get_db` yields a session.
- **`test_scoping.py`** — **the isolation proof**: a scope only sees its own account's docs; `.query`/`.select` reject models without `account_id`; `.owns`.
- **`test_routes.py`** — `/health`; `/api/v1/me` auth gates (401 no/bad token, 400 multi-account needs header, 200 correct, 403 non-member).
- **`test_ocr_routing.py`** — `choose_engine` matrix, `probe_pdf_text_layer`, `extension_for`, `detect_language`; **native-PDF block bboxes**; **Vision per-page partial tolerance** (skip a failed page / all-fail raises / `failed_pages` cache round-trip). Vision seam stubbed; **no network.**
- **`test_documents.py`** — upload→OCR→**chained extraction→embedding** (text-layer PDF; `tmp_storage` stubs `extraction.call_extraction_model` **and** `embeddings.embed_passages` → doc lands in `needs_review`), dedup (200 + same id), 415 unsupported, **413 over-cap**, 400 empty, 401 unauth, account isolation on detail+list.
- **`test_retry.py`** — `with_retry`: immediate success, flaky-then-success, fail-fast on non-retryable, exhaustion, backoff cap (injected `sleep`/`rng`).
- **`test_concurrency.py`** — `map_bounded`: order preservation, empty/single-worker fast paths, concurrency capped at `max_workers`.
- **`test_storage.py`** — `save_stream`: content-addressing, incremental hash == full-bytes hash, oversized → `FileTooLargeError` + temp cleanup.
- **`test_reprocessing.py`** — status routing to entry points, full `ocr_done→indexed` re-drive, `failed` clears error, account-scoped sweep skipping terminal docs.
- **`test_eval.py`** — scorer units (recall/correctness/`None`-aggregation/missing-result/bad-type) + runner end-to-end vs the stub.
- **`test_embeddings.py`** (10 tests) — passage/query asymmetry (`embed_query` prefixes `QUERY_INSTRUCTION`, `embed_passages` doesn't, empty no-op); live-DB `run_embedding` (facts + summary get 768-vecs, status → `indexed`); no-facts doc (summary only); idempotent re-index; **`needs_review` embedded but flag preserved**; unindexable status no-op; account isolation; **extraction→embedding chain** reaches `indexed` (and embeds a `needs_review` doc while keeping the flag); **thread-safe singleton load** (8 threads → one load). Encoder stubbed via `embed_passages`/`_encode` → no model download.
- **`test_extraction.py`** — `parse_extraction` (lenient enums/dates, numeric coercion + clamp, code-fence stripping); **`_bbox_for_fact`** matches native-PDF blocks; **`chunk_pages`** / **`merge_results`** (split/union/dedup, chunk-order merge); live-DB fan-out (tables written, unknown slug dropped, org deduped); **idempotent re-run**; **`needs_review` routing**; **account isolation**; **multi-chunk run** (all pages covered); **partial tolerance** (one chunk fails → partial card; all fail → `failed`); card endpoint. LLM + encoder mocked → fully offline.

---

## 5. End-to-end workflow (current)

**Upload (Flow A, implemented through `extracted`):**
1. `POST /api/v1/documents` (multipart) → auth + `AccountScope` resolved.
2. Validate MIME → **stream** body to disk via `save_stream` (incremental sha256, `max_upload_mb` cap → 413, atomic write to `storage/<account>/<hash><ext>`).
3. **Dedup**: if `(account_id, file_hash)` exists → return it (200). Else insert `documents` row at `received`, log `processing_events(received, succeeded)`, commit (201).
4. `BackgroundTask` → `run_ocr(document_id, account_id)`: check OCR cache by hash → else route (PDF text-layer probe → Vision fallback / docx / image→Vision) → write `ocr_text`, `ocr_engine`, `page_count`, `language`; status → `ocr_done`; log `processing_events(ocr, succeeded)`. Vision block bboxes + full per-page artifact saved to `storage/ocr_cache/<hash>.json` for later provenance.
5. **Chained** → `run_extraction(document_id, account_id)`: DeepSeek structured pass(es) over the OCR text — **page-window chunked** for long docs (one call per ~14k-char chunk, results merged) → card + atomic facts fanned into `document_classes`/`entities`/`document_entities`/`document_dates`/`typed_facts`/`document_facts`; `extraction_raw`/`extraction_model`/`title`/`summary` written; status → `extracted` (or `needs_review`); log `processing_events(extraction, succeeded)`.
6. **Chained on `extracted`** → `run_embedding(document_id, account_id)`: bge-base embeds every `document_facts.text` → `embedding` and `summary` → `summary_embedding`; status → `indexed`; log `processing_events(embedding, succeeded)`.
7. `GET /api/v1/documents` (light list) / `GET /api/v1/documents/{id}` (full card) to read back (account-scoped).

**Chat (Flow B):** not built yet (Phase 5).

---

## 6. Schema (already applied — do not recreate)

`schema.sql` / `alembic/versions/0001_initial_schema.py` define **21 tables + `v_document_pipeline` view** at `vector(768)` (for `bge-base-en-v1.5`). Embeddings model is locked at **768-dim**. Migration **`0002`** adds the `documents.summary_embedding` HNSW cosine index (`m=16, ef_construction=64`), matching the one on `document_facts.embedding` — so **both vector-retrieval stages are indexed** (`summary_embedding` to pick docs → `document_facts.embedding` to rank facts). `alembic upgrade head` is the apply path; **all schema changes go through new Alembic migrations** (additive; ask before destructive ops). `plans` table is seeded (`free`/`pro`/`team`) by the migration. Tables: identity/tenancy (`accounts`, `users`, `account_members`, `classes`), documents/card (`documents`, `document_classes`, `entities`, `document_entities`, `document_dates`, `typed_facts`), retrieval (`document_facts` — HNSW cosine + GIN fts), chat/observability (`conversations`, `messages`, `retrieval_traces`, `processing_events`), feedback/billing (`answer_ratings`, `usage_events`, `plans`, `subscriptions`, `invoices`, `usage_counters`).

---

## 7. Runtime model routing (decided; wire up in Phase 3+)

| Role | Model | Key | Status |
|---|---|---|---|
| OCR | Google Vision | `GOOGLE_APPLICATION_CREDENTIALS` | ✅ live (Phase 2) |
| Extraction (cheap structured pass) | **DeepSeek** `deepseek-chat` (OpenAI-compatible client, custom `base_url`) | `DEEPSEEK_API_KEY` | ✅ live (Phase 3) |
| Intent routing + standard synthesis | **Gemini 2.5 Flash** | `GEMINI_API_KEY` | key set, not wired |
| Hard synthesis / low-confidence re-check | **GPT-4o** | `OPENAI_API_KEY` | key set, not wired |
| Embeddings | `bge-base-en-v1.5` (local, 768-d, CPU) | — | ✅ live (Phase 4); `sentence-transformers`/`torch` installed |

Cost discipline: cheap model for extraction, strong only for hard synthesis, local embeddings.

---

## 8. Next steps — Phase 5 (Retrieval + synthesis)

Goal: answer a user query by retrieving against the now-indexed structured data, with citations. Per `TECH_SPEC.md` §Retrieval:
1. **Intent router** (cheap model / rules) → `metadata | semantic | hybrid | aggregate`. New `app/services/retrieval.py`.
2. **Structured-first** (no LLM): aggregate/metadata queries hit `typed_facts` (`value_numeric` for sums/compares), `document_dates`, `entities`, `document_classes` directly. *This is where "how much did I spend" belongs — not vector search* (the bge smoke confirmed vague amount queries mis-rank against item names).
3. **Lexical**: Postgres FTS over `document_facts.fts` (GIN, already generated) for exact ids/names.
4. **Vector, two-stage**: `embed_query(q)` → `documents.summary_embedding` (HNSW) picks candidate docs → `document_facts.embedding` (HNSW) ranks facts within them. Use `embed_query` (instruction-prefixed), **not** `embed_passages`.
5. **Rerank** merged candidates (cross-encoder, e.g. bge-reranker) → top facts.
6. **Synthesize** (Gemini 2.5 Flash standard / GPT-4o hard) over top facts + metadata → answer with **mandatory citations** (each fact already carries `document_id`/`page`/`bbox`).
7. **Persist + stream**: map `conversations`/`messages`/`retrieval_traces` ORM tables; write a trace per answer; emit the pipeline stages as SSE events (intent → shortlist → reading → tokens → citations) — the foundation for the real-time "subprocess" UX and shortlist-confirmation/guided-narrowing.
8. Wire `gemini_api_key` (field exists) into a Gemini client; add a model-routing seam (cheap vs. hard synthesis). Mock all LLM/encoder seams in tests.

---

## 9. Known follow-ups / debts

- **Error envelope inconsistency** — endpoints raise `HTTPException(detail={"code","message"})` → renders as `{"detail":{...}}`, but `API_CONTRACTS.md` specifies `{"error":{"code","message"}}`. Consistent across the app but doesn't match the contract; decide and add a global handler. *(Deliberately out of scope for the cleanup pass — owner doing API/auth later.)*
- **Background processing** is FastAPI `BackgroundTasks`, not the planned Redis worker — fine for now; revisit when volume/durability matters. The pipeline chains **OCR → extraction → embedding** in one in-process background task (synchronous under TestClient), so the whole chain runs on one worker thread; the network fan-out *within* a doc is bounded-parallel. The **bge model loads lazily on first real embedding** (~400 MB) — first upload after a restart pays that cost. **Re-drive after a crash:** `python -m scripts.reprocess` (BackgroundTasks don't survive a restart).
- **Embeddings validated on real facts** (Phase 4) — bge-base produces 768-d normalized vectors with sensible retrieval rankings (subscription-cost and NDA-term queries nailed their fact). A vague "how much did I spend" query mis-ranked a grocery-item fact above the amount — **expected**, and the reason aggregate/amount queries route to `typed_facts` SQL in Phase 5, not vector search.
- **Atomic-fact bbox is best-effort** — `_bbox_for_fact` token-overlaps the model's paraphrased fact against OCR-cache blocks on its page (≥0.5 overlap); below threshold → null. **Both native-PDF and Vision pages now carry block bboxes**, so provenance is box-level for all input types when the overlap matches (paraphrase-heavy facts may still fall back to page-only).
- **Doc-level summary = first chunk's summary** — for chunked (long) docs, `merge_results` takes the first non-empty chunk summary rather than synthesizing across chunks. Fine for now (page-1 intro is usually representative); a dedicated summary-merge pass would improve long-doc summaries.
- **Extraction validated on real docs** (Phase 3) via a throwaway smoke harness over `storage/samples/` (3 PDFs + 2 receipt JPEGs) against **live Vision + DeepSeek** — receipts/contract/reports all classified + extracted accurately; reports in `storage/samples/reports/` (gitignored). Quality is good; sparse pages (code listings, rubrics) legitimately yield no atomic facts.
- **List endpoint vs. contract** — `GET /documents` returns light `DocumentOut` items (not full cards) to avoid N+1; `API_CONTRACTS.md` shows `[DocumentCard]`. Detail returns the full card. Fine for now; batch-load if the list view needs card data.
- **`gemini_api_key` field added** (Phase 3) but **Gemini still not wired** — that's Phase 5 (intent routing + synthesis).
- **Auth is dev-grade** (bearer = user UUID). Replace at the `get_current_user` seam before any real deployment; consider RLS as defence-in-depth alongside `AccountScope`. *(Owner doing security/auth deliberately later.)*
- **Eval gold set is illustrative scaffold** — `eval/gold/seed.yaml` `expected_doc_ids` are slugs, not real UUIDs; the runner uses a fixture stub. Phase 5 seeds an eval corpus and wires `retrieve(query, account_id)` (see `eval/README.md`).
