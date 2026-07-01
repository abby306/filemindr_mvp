# STATUS.md — Development State

> **Purpose:** a self-contained handoff for the next agent/session. Read this before touching code so you don't have to re-traverse the whole repo. Update it at the end of each development cycle.
>
> **Last updated:** 2026-06-30 · **Phase 5 fully complete and merged to `main`** — hybrid retrieval (intent router + structured/lexical/vector + RRF) → cross-encoder reranking → **agentic synthesis** (Gemini 2.5 Flash) with **GPT-4o escalation**, conversation memory, and the full chat HTTP surface (endpoints + **SSE streaming** + `retrieval_traces` writes + ratings). README brought current.
>
> **Git state:** `main` holds everything through PR #4 (merged: chat HTTP · SSE/escalation/ratings/eval · README · extraction entity-coercion fix). **One branch is unmerged — `feat/classes-api` (PR #5): the class-catalog API + `?class=` document filter — awaiting the owner's local test then merge.** Nothing else pending; working tree clean.
>
> **Verified on a real 23-doc corpus** (`scripts/seed_corpus.py` → live OCR/DeepSeek/bge; invoices, bank statement, receipts, NDA, technical plans, thesis, lab work): live chat answers accurate with grouped citations, `supported` flag, and working "it"-style follow-ups; GPT-4o escalation verified live. **179 tests passing, offline.**
>
> **Temporary testing UI (NOT committed — localhost only):** `dev_ui/index.html` is a throwaway single-file vanilla HTML/JS harness for the owner to test retrieval by hand — served same-origin at **`/dev/`** in development. It is **git-ignored on purpose** (`dev_ui/` in `.gitignore`); do not commit it or treat it as the real frontend (that's Phase 6, Next.js). It exercises: document list, **class browser (filter docs by class, create/delete custom classes)**, **per-document card view** (classes+confidence, entities, dates, typed facts), chat with live SSE steps, and the 👍/👎+stars+reasons rating widget.

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
| 5 — Retrieval + synthesis | ✅ **Done** | intent router + hybrid retrieval + RRF + reranker; agentic synthesis (Gemini Flash + tools) with **GPT-4o escalation**; conversation memory; **chat endpoints + SSE streaming + `retrieval_traces` writes**; **ratings endpoint**; synthesis eval wired |
| 6 — Frontend (Next.js) | ⏭️ Pending | Upload / Document view / Ask / Ratings |
| 7 — Analytics + billing | ⏭️ Pending | usage counters, plans, quotas |

- **Tests:** 179 passing (`pytest -q`) on `main` + the `feat/classes-api` branch. Run against the **live local Postgres**. Offline — Vision, DeepSeek, the bge encoder/reranker, **and the Gemini + GPT-4o synthesis seams** are all mocked.
- **Document pipeline status flow:** `received → ocr_done → extracted → indexed` (+ `failed` / `needs_review`). The full chain auto-runs on upload: OCR → extraction → embedding. **Every successfully-extracted doc is embedded** (so it is retrievable); confident docs reach **`indexed`**, low-confidence ones are embedded but **stay `needs_review`** (searchable + flagged for human review). Stuck/`failed` docs can be re-driven idempotently (`scripts/reprocess.py`).
- **Resilience:** transient DeepSeek/Vision failures are retried (bounded backoff); a single failing chunk/page is skipped + recorded rather than failing the whole doc (only all-fail → `failed`). Per-chunk extraction and per-page OCR run with bounded concurrency. Uploads stream to disk with a size cap.
- **DB:** 22 tables + `v_document_pipeline` view; migration `0002` adds the `documents.summary_embedding` HNSW index (both vector stages now indexed — see §6). `python -m scripts.seed` creates 1 dev user, personal + company accounts, 14 system classes each, 0 documents. **The Personal account currently holds a live 23-doc corpus** (loaded via `python -m scripts.seed_corpus`; 21 `indexed`, 2 `needs_review`, 0 failed; 468 atomic facts). Re-run `seed_corpus` (idempotent) to reload.
- **New dependency:** `google-genai` (Gemini SDK) — `pip install google-genai` (no `requirements.txt` in repo; deps live in the venv). Needs `GEMINI_API_KEY` in `.env` (set). Vision creds are now wired explicitly from settings (`config.vision_credentials_path` → `ocr._vision_client`) — image OCR no longer depends on an ambient `GOOGLE_APPLICATION_CREDENTIALS` env var.

---

## 3. Environment & how to run

- **Machine:** Ubuntu, Python 3.12 (pyenv), venv at `.venv`. **Native services, no Docker this phase.**
- **Postgres 16 + pgvector** (`vector` 0.8.3) at `postgresql+psycopg://filemindr:localdev@localhost:5432/filemindr`.
- **Redis** at `localhost:6379` — installed but **not used yet** (background work currently uses FastAPI `BackgroundTasks`, not a Redis worker).

```bash
source .venv/bin/activate
python -m scripts.seed            # idempotent: dev user, 2 accounts, default classes
uvicorn app.main:app --reload     # serves on :8000 (dev UI at /dev/ if dev_ui/ exists)
pytest -q                         # 179 tests
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
- **`models.py`** — ORM mapped to the **existing** schema. **Never `create_all`** — Alembic owns DDL. `Base = DeclarativeBase`. Postgres enums via `_pg_enum(..., create_type=False)`. **Mapped tables:** `Account`, `User`, `AccountMember`, `Class`, `Document`, `DocumentClass`, `Entity`, `DocumentEntity`, `DocumentDate`, `TypedFact`, `DocumentFact` (has `embedding Vector(768)`; `fts` tsvector is DB-generated, not mapped), `ProcessingEvent` (bigserial PK; append-only pipeline log), **`Conversation`** + **`Message`** (chat memory; `message_role` enum `user|assistant`). **`RetrievalTrace`** (`retrieval_traces`; one row per answered message — intent/plan/candidates/citations/model/tokens/latency, JSONB cols), **`AnswerRating`** (`answer_ratings`; `rating_value` enum, `reasons text[]`). **Not yet mapped** (add when their phase arrives): `usage_events`, `plans`, `subscriptions`, `invoices`, `usage_counters`. Membership relationships use `passive_deletes=True` (defer to DB `ON DELETE CASCADE`).

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
  - `parse_extraction(raw) → ExtractionResult` — **pure, lenient, unit-tested**: clamps confidence to [0,1], coerces `value_numeric`, bad enums → defaults (`value_type`→`string`, date `role`→`mentioned`), unparseable dates → null, strips stray code fences, and **coerces entity objects `{"name": "X"}` → `"X"`** (DeepSeek sometimes returns nested entities; without this one odd shape failed the whole doc — found live on a receipt). Pydantic sub-models: `ClassPrediction`, `EntityGroups` (has the entity-name `mode="before"` validator), `DatePrediction`, `TypedFactPrediction`, `AtomicFactPrediction`.
  - **Page-window chunking (long docs):** `chunk_pages(pages, budget=14k) → [PageChunk]` packs whole pages (never split) into chunks marked with `===== PAGE n =====`; `merge_results([ExtractionResult]) → ExtractionResult` unions the per-chunk cards (classes keep max confidence/slug; entities/dates/typed/atomic facts deduped; title/summary = first non-empty). Both **pure + unit-tested**. A doc under budget is a single chunk (one call) — unchanged behavior; only long docs fan out. This fixed multi-page coverage (the 20-page sample went from facts truncated at page 10 → facts across all 20 pages; the 5-page NDA from 1/5 → 5/5 pages covered).
  - `build_messages(text, classes)` — pure; hard safety ceiling 50k chars/call; injects the account's class catalog (slug: description) and instructs the model to attribute each fact to its `===== PAGE n =====` marker.
  - **`call_extraction_model(text, classes) → (raw_json, model_name)`** — the **only network seam** (DeepSeek via `OpenAI(base_url=…)`, `response_format=json_object`, temp 0). Wrapped in `with_retry` (`_is_transient_llm` predicate); chunks are extracted **in parallel** (`map_bounded`, cap `max_parallel_calls`) with results kept in chunk order. **Partial-tolerant:** a chunk that fails after retries is recorded in `extraction_raw.failed_chunks` + event detail and skipped; only an all-chunk failure → `failed`. Monkeypatched in tests.
  - Fan-out writers (all account-scoped): `_write_card` (classes→`document_classes` matching catalog slugs, **unknown slugs dropped**; entities upserted into `entities` by `(account, type, normalized_name)` then linked via `document_entities`, deduped per doc; dates→`document_dates`; typed facts→`typed_facts`), `_write_atomic_facts` (→`document_facts`, **best-effort bbox** via token-overlap match against OCR-cache blocks on the fact's page; `embedding` stays null until Phase 4).
  - `_route_status` → `extracted` if top-class confidence ≥ `REVIEW_CONFIDENCE` (0.5), else `needs_review` (also `needs_review` when no class predicted).
  - **`run_extraction(document_id, account_id)`** — background entry point: own session, account-scoped, **idempotent** (clears the prior card before rewriting, so re-runs are clean). Loads the OCR-cache artifact → `chunk_pages` → one `call_extraction_model` per chunk → `merge_results` → fan-out (no cache ⇒ single-chunk fallback over `ocr_text`). Saves `extraction_raw` (`{chunk_count, chunks:[parsed JSON per chunk]}`), `extraction_model`, `title`, `summary`; logs `processing_events(extraction, …)` with the chunk count; on exception sets `failed`. Re-extractable from `ocr_done`/`extracted`/`needs_review`. **On `extracted` (not `needs_review`), chains `embeddings.run_embedding`** (local import).
- **`embeddings.py`** — Phase-4 local embeddings (**`BAAI/bge-base-en-v1.5`**, 768-d, CPU; lazy singleton). Key pieces:
  - **Asymmetric encoding** (bge convention, matters for retrieval accuracy): `embed_passages(texts)` for indexing (no prefix); `embed_query(q)` for Phase-5 search (prepends `QUERY_INSTRUCTION`). Both go through `_encode` (the single compute seam, **`normalize_embeddings=True`** so cosine distance is exact) — tests stub it, never downloading the model. The model is a **thread-safe** lazy singleton (`_get_model` uses double-checked locking around `_load_model`), so concurrent first uploads load it once.
  - **`run_embedding(document_id, account_id)`** — background entry point: own session, account-scoped, idempotent (overwrites vectors in place). Embeds all `document_facts.text` → `embedding` and `documents.summary` → `summary_embedding`; status → `indexed`. A `needs_review` doc is still embedded (searchable) but **keeps its review flag** rather than flipping to `indexed`. Logs `processing_events(embedding, …)`; on exception sets `failed`. Re-indexable from `extracted`/`indexed`/`needs_review`.

- **`retrieval.py`** — Phase-5 hybrid retrieval (no LLM). `classify_intent(query)` → `aggregate|lexical|metadata|semantic` (pure regex rules; ALL-CAPS proper nouns + `vat/tax` routing). Three retrievers, all account-scoped & optionally **scoped** (`document_ids` / `class_slug` via `_resolve_scope`): **vector two-stage** (`summary_embedding` HNSW shortlist → `document_facts.embedding` HNSW rank), **lexical** (FTS over `document_facts.fts` **plus** `typed_facts.value` / `entities.name` exact-match — exact ids/parties often live only there), **structured** (`typed_facts`/`dates`/`entities` from the most-relevant docs, ordered by doc-relevance then label-match priority; a typed fact whose label the query names is marked `exact` and fused as a high-weight source). Fused with intent-weighted **RRF** (`rrf_merge`), then **reranked** (`rerank=True` default). `retrieve(query, account_id, *, k, rerank, document_ids, class_slug) → RetrievalResult` (facts: `list[FactHit]`, doc_ids, plan). Pure helpers (`classify_intent`, `rrf_merge`) unit-tested; `embed_query` is the only model seam.
- **`reranking.py`** — Phase-5 cross-encoder (`BAAI/bge-reranker-base`, local CPU, lazy thread-safe singleton). `rerank(query, hits, *, top_k)` **blends** cross-encoder relevance with the incoming RRF score: `0.4·CE + 0.6·fused` (both min-max normalized) — consensus-primary, because the small reranker is brittle (under-scores answers buried in a clause; can't read terse `label: value` text), so it *refines* but can't bury a strong-consensus fact. `_score` is the only seam (tests stub it).
- **`catalog.py`** — Phase-5 document catalog (corpus awareness the agent *queries*, not a context dump). `find_documents(db, account_id, *, class_slug, name, about, uploaded_after, uploaded_before, limit)` → `CatalogDoc[]` (resolves human refs: class / remembered name / upload window / semantic `about` via `summary_embedding`). `corpus_overview(db, account_id)` → bounded orientation (counts, by-class, date range; inlines the **full** listing when `total ≤ SMALL_CORPUS=30`, else stats + recent). Considers only **searchable** docs (`indexed`/`needs_review`).
- **`synthesis.py`** — Phase-5 **agentic** synthesis (Gemini 2.5 Flash). `synthesize(query, account_id, *, db, history, model, max_steps, document_ids) → SynthesisResult` (answer, `supported`, `citations`, intent, searches, documents_looked_up, tokens, latency). Seeds the model with a corpus overview + initial candidate pool + conversation `history`, then loops (bounded `_MAX_STEPS=5`, forced `finish` on the last turn) with three tools: **`find_documents`**, **`search`** (scoped via `document_ref`/`class`), **`finish`**. Grounding by construction: short ids (`f3`/`d2`) via `_FactRegistry`/`_DocRegistry`; cited ids validated → real `document_id`/`page`/`bbox`; hallucinated ids dropped; `supported=false` is the honest "not in your docs" path; `function_calling` mode ANY. **`_gemini_turn` is the only network seam** (tests stub it). For small corpora the inlined overview means `find_documents`/`search` often don't need to fire — they activate at scale. **`document_ids`** pins the initial `retrieve` (and tells the agent it's scoped) — the path behind the message endpoint's `scope="document"`. The loop lives in **`synthesize_iter`** (a generator that **yields step events** — `intent`/`find_documents`/`searching`/`escalating`/`result`); `synthesize()` just drains it (the SSE endpoint forwards the events). On a `supported=false` finish it **escalates to GPT-4o** (`_openai_resynthesize` — single-shot over the candidate pool, the only new network seam, tests stub it): adopts GPT-4o's answer only if it grounds it (`escalated=True`, `model=gpt-4o`), else the honest miss stands. `SynthesisResult` now also carries `candidate_facts`/`plan`/`escalated` for the trace.
- **`conversations.py`** — Phase-5 conversation memory. `create_conversation`, `add_message` (account-scoped, sets explicit `created_at` so same-transaction user→assistant order is deterministic — uuid pks aren't monotonic), `load_history(... limit=12)` (windowed, oldest-first), **`record_trace(db, account_id, message_id, result)`** (one `retrieval_traces` row from a `SynthesisResult` — now incl. `candidates`/enriched `retrieval_plan`/`context_sent`), **`chat(...)`** → `(SynthesisResult, conversation_id, assistant_message_id)`: loads windowed history → `synthesize(history=…, document_ids=…)` → persists both turns + the trace (atomic), and **`chat_stream(...)`** — a generator (own session) that forwards `synthesize_iter`'s events, then persists messages+trace and yields a final `done` event (the SSE path). Creates the conversation if none.

### `app/api/` — HTTP layer
- **`schemas.py`** — Pydantic response models. `DocumentOut` (id, status, source, original_filename, mime_type, byte_size, title, summary, language, page_count, created_at — `from_attributes=True`) is the light list/ingest view. `DocumentListOut` (items + next_cursor). **`DocumentCardOut`** (extends `DocumentOut`) adds `classes` (`ClassCardOut` slug/name/confidence), `entities` (`EntitiesCardOut` people/orgs/places), `dates` (`DateCardOut`), `typed_facts` (`TypedFactCardOut`; `value_type`→`type`), and `fact_count` — returned by the document-detail endpoint.
- **`documents.py`** — `APIRouter(prefix="/api/v1")`. Endpoints:
  - `POST /documents` — multipart upload. `_resolve_mime` (header → extension fallback); 415 if unsupported. **Streams** the upload via `save_stream` in a threadpool with `max_upload_mb` cap → **413** if over cap, **400** if empty. **Dedup** on `(account_id, file_hash)` (returns existing with **200**); else insert `Document` at `received`, `record_event(received, succeeded)`, commit, then **schedule `ocr.run_ocr` as a `BackgroundTask`**, return **201**.
  - `GET /documents` — account-scoped list, newest first, `status` **and `class`** (slug) filters, **keyset pagination** (opaque base64 cursor of `(created_at, id)`), `limit` 1–200.
  - `GET /documents/{id}` — account-scoped detail returning the **full `DocumentCardOut`** (`_build_card` assembles classes/entities/dates/typed_facts/`fact_count` via scoped queries); **404** for another account's doc. Card sections are empty until extraction runs.
- **`conversations.py`** — `APIRouter(prefix="/api/v1")`, chat surface (thin wrappers over `services/conversations`). Endpoints:
  - `POST /conversations` → **201** `{id}` (scoped to the active account).
  - `POST /conversations/{id}/messages` — one agentic turn over `conversations.chat`. Body `{content, scope?:"account"|"document", document_id?}`; `scope="document"` validates `document_id` (**400** if missing, **404** if not in account) and pins retrieval to it. Returns `{message_id, answer, citations[], supported}`; **404** for an unknown/foreign conversation. (Scope/conversation validation factored into `_resolve_document_scope`/`_require_conversation`.)
  - `POST /conversations/{id}/messages/stream` — same inputs, but **SSE** (`text/event-stream`): emits `intent` → `find_documents`/`searching` → (`escalating`) → `done` (final answer+citations). Validates scope/conversation up front, then streams `conversations.chat_stream`.
  - `GET /conversations/{id}/messages` — account-scoped full history (oldest-first `MessageOut[]`); **404** for an unknown/foreign conversation.
  - `POST /messages/{id}/rating` — attach feedback `{rating:"up"|"down", stars?, reasons?, comment?}` → writes `answer_ratings` (account-scoped, **404** for a foreign/unknown message); returns `{ok:true}`.
- **`classes.py`** — `APIRouter(prefix="/api/v1")`, class-catalog management. `GET /classes` (account's classes, system first, each with `document_count` via one grouped query), `POST /classes` (`{name, description?}` → slug derived by `_slugify`; **409** on slug conflict incl. system-slug collision, **400** on empty slug; `is_system=false`), `DELETE /classes/{id}` (**404** if not in account, **409** if `is_system` — system classes immutable; cascades `document_classes` links). The `Class` ORM was already mapped; **no migration**.
- **`schemas.py`** also has chat models (`ConversationOut`, `MessageCreate`, `CitationOut`, `MessageAnswerOut`, `MessageOut`, `MessageRatingIn`, `OkOut`) and class models (`ClassOut`, `ClassCreate`).

### `app/main.py`
FastAPI app. `app.include_router(...)` for documents, conversations, and classes. **Dev-only:** when `app_env == "development"` **and** a (git-ignored) `dev_ui/` dir exists, mounts it at **`/dev/`** (`StaticFiles`, same-origin) for the throwaway testing UI — inert otherwise. `GET /health` (unauth; `SELECT 1` DB check → 200/503). `GET /api/v1/me` (auth+scoping demo, returns user+account). **Note:** FastAPI 0.138 represents included routers as a lazy `_IncludedRouter` in `app.routes`; verify routes via `app.openapi()["paths"]`, not by scanning `app.routes`.

### `scripts/`
- **`seed.py`** — idempotent `python -m scripts.seed`. Creates dev user (`abdullahasad70@gmail.com`), personal + company accounts, memberships (both `owner`), and the 14 system classes per account. Prints the dev-user UUID (bearer token) + account UUIDs.
- **`reprocess.py`** — `python -m scripts.reprocess [--statuses ..] [--account ..]`. Sweeps stuck/`failed` docs via `reprocessing.reprocess_stuck`.
- **`seed_corpus.py`** — `python -m scripts.seed_corpus [--account ..|--account-name ..]`. Ingests `storage/samples/*` through the **live** OCR→extraction→embedding chain into an account (default Personal); idempotent (dedups, re-drives non-terminal). Prints a `file → doc_id → status → fact_count` table. (`.pptx`/`.txt` are skipped — unsupported.)
- **`retrieve.py`** — `python -m scripts.retrieve [--account ..] [--k N] "<query>"`. Prints intent + top-k facts (doc/page/score/source). The retrieval-only rating loop (no synthesis).
- **`eval_retrieval.py`** — `python -m scripts.eval_retrieval [--k N] [--gold ..] [--doc-map ..]`. Scores live retrieval vs `eval/gold/seed.yaml`; auto-maps gold slugs → real doc UUIDs by token overlap (override with `--doc-map`). `answer_correctness` reads low until synthesis is wired into the eval; watch `doc_recall`/`fact_recall`.
- **`ask.py`** — `python -m scripts.ask [--account ..] "<query>"`. One-shot **agentic** answer (live Gemini): answer + supported + citations + searches/lookups + tokens.
- **`eval_synthesis.py`** — `python -m scripts.eval_synthesis [--k N] [--doc-map ..]`. Like `eval_retrieval` but runs the full `synthesize` per gold query (reuses its slug→doc mapping), so **`answer_correctness`** is finally scored (live Gemini/GPT-4o; not in `pytest`).
- **`chat.py`** — `python -m scripts.chat [--account ..] [--conversation <id>]`. Interactive multi-turn chat with memory (live Gemini); prints the conversation id to resume later.

### `eval/` — retrieval eval harness (built pre-Phase-5; see `eval/README.md`)
- **`schema.py`** — `GoldQuery`/`RetrievedAnswer`; `load_gold(path)` (YAML).
- **`scorers.py`** — pure `recall_at_k` (doc + fact-substring), `answer_correctness`, `score_dataset` (per-type + overall, `None`-aware means; `normalize()` is the LLM-judge seam).
- **`gold/seed.yaml`** — 8 illustrative queries across the 4 intents, grounded in the Phase-3/4 sample docs (`expected_doc_ids` are slugs → map to real UUIDs in a seeded eval corpus).
- **`run.py`** — `python -m eval.run [--k N] [--gold path]`; scores a `retrieve(query)` callable, ships a fixture stub. **Phase 5 wiring point** documented in the README.

### `tests/` (179 tests, all live-DB; every network/model seam mocked)
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
- **`test_retrieval.py`** — `classify_intent` matrix; `rrf_merge` (weighted order, zero-weight exclusion, prefer-citable-copy); live-DB `retrieve` (vector ranks aligned fact first, lexical exact-id, aggregate surfaces typed fact, lexical entity-name match, aggregate label priority, **document scoping**, account isolation). `embed_query` stubbed; deterministic RRF tests pass `rerank=False`.
- **`test_reranking.py`** — `rerank` reorders/respects top_k/empty no-op/passes (query,fact) pairs; live-DB `retrieve(rerank=True)` integration (blend promotes the right fact). `_score` stubbed.
- **`test_synthesis.py`** — agentic loop with `_gemini_turn` **and** `retrieve` stubbed: finish-now-with-citation, search-then-finish, hallucinated-citation dropped, unsupported answer, bounded-loop forced finish, token accumulation; **`synthesize_iter` event sequence**; **GPT-4o escalation** (adopt when Flash misses + GPT-4o grounds; keep the honest miss when it can't). (`corpus_overview` + `_openai_resynthesize` stubbed in `no_db`.)
- **`test_catalog.py`** — `find_documents` by class / name / upload window / semantic `about` (embed_query stubbed); `corpus_overview` small-corpus inlining + excludes unsearchable.
- **`test_conversations.py`** — create/history roundtrip, windowed + ordered, account-scoped, foreign-account rejected; **`chat`** persists both turns + passes prior history, **writes a `retrieval_traces` row**, and **threads `document_ids`** (synthesize stubbed).
- **`test_conversations_api.py`** — chat HTTP surface (synthesize/`synthesize_iter` stubbed): create (201 + auth gate); `POST messages` returns answer/citations/supported + persists messages + **writes the trace row**; unknown/foreign conversation → 404; `scope="document"` (400 no id / 404 bad doc / threads `document_ids`); `GET messages` history + account isolation; **`POST messages/stream`** returns `text/event-stream`, emits intent→searching→done, persists messages + one trace (and 404s an unknown conversation up front).
- **`test_ratings_api.py`** — `POST /messages/{id}/rating` persists (rating/stars/reasons/comment); minimal thumbs-up; **404** unknown/foreign message; **422** bad enum; account isolation.
- **`test_classes_api.py`** — `GET /classes` (empty + auth + `document_count`); `POST` (slug derivation, **409** duplicate, **400** empty slug); `DELETE` (custom 204, **409** system-immutable, **404** unknown); account isolation; **`GET /documents?class=<slug>`** filter.

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

**Chat (Flow B, now exposed over HTTP):**
1. `conversations.chat(account_id, user_message, conversation_id?)` → creates/loads the conversation, reads **windowed history** (last 12 turns).
2. `synthesis.synthesize(query, account_id, history=…)`:
   a. `catalog.corpus_overview` (bounded; inlines the full catalog when ≤30 docs) + initial `retrieval.retrieve(query, k=12)` seed the candidate pool (facts get short ids `f#`, docs `d#`).
   b. Agentic loop (Gemini Flash, ≤5 turns, forced `finish` last): the model may `find_documents(...)` to resolve a reference, `search(query, document_ref?/class?)` for more (scoped) facts, then `finish(answer, cited_fact_ids, supported)`.
   c. Citations validated against the registry → real `document_id`/`page`; `supported=false` when the corpus lacks the answer.
   d. If the Flash loop returns `supported=false`, **escalate** to GPT-4o (single-shot re-synth over the candidate pool); adopt its answer only if grounded.
3. Persist user + assistant messages + a `retrieval_traces` row; return `SynthesisResult`. **Drivers:** the chat endpoints (`POST /conversations`, `.../messages`, `.../messages/stream` (SSE), `GET .../messages`, `POST /messages/{id}/rating`), the dev UI at `/dev/`, `python -m scripts.chat` (interactive), `python -m scripts.ask` (one-shot), `python -m scripts.eval_synthesis` (answer_correctness).

**Eval (retrieval quality):** `python -m scripts.eval_retrieval` scores live retrieval vs the gold set; `python -m scripts.retrieve "<q>"` inspects ranked facts.

---

## 6. Schema (already applied — do not recreate)

`schema.sql` / `alembic/versions/0001_initial_schema.py` define **21 tables + `v_document_pipeline` view** at `vector(768)` (for `bge-base-en-v1.5`). Embeddings model is locked at **768-dim**. Migration **`0002`** adds the `documents.summary_embedding` HNSW cosine index (`m=16, ef_construction=64`), matching the one on `document_facts.embedding` — so **both vector-retrieval stages are indexed** (`summary_embedding` to pick docs → `document_facts.embedding` to rank facts). `alembic upgrade head` is the apply path; **all schema changes go through new Alembic migrations** (additive; ask before destructive ops). `plans` table is seeded (`free`/`pro`/`team`) by the migration. Tables: identity/tenancy (`accounts`, `users`, `account_members`, `classes`), documents/card (`documents`, `document_classes`, `entities`, `document_entities`, `document_dates`, `typed_facts`), retrieval (`document_facts` — HNSW cosine + GIN fts), chat/observability (`conversations`, `messages`, `retrieval_traces`, `processing_events`), feedback/billing (`answer_ratings`, `usage_events`, `plans`, `subscriptions`, `invoices`, `usage_counters`).

---

## 7. Runtime model routing (decided; wire up in Phase 3+)

| Role | Model | Key | Status |
|---|---|---|---|
| OCR | Google Vision | `GOOGLE_APPLICATION_CREDENTIALS` | ✅ live (Phase 2) |
| Extraction (cheap structured pass) | **DeepSeek** `deepseek-chat` (OpenAI-compatible client, custom `base_url`) | `DEEPSEEK_API_KEY` | ✅ live (Phase 3) |
| Intent routing | rules (regex) | — | ✅ live (Phase 5); pure, no LLM |
| Standard synthesis (agentic) | **Gemini 2.5 Flash** (`google-genai`) | `GEMINI_API_KEY` | ✅ live (Phase 5) |
| Hard synthesis / low-confidence re-check | **GPT-4o** | `OPENAI_API_KEY` | ✅ live (Phase 5); single-shot re-synth over the candidate pool when Flash returns `supported=false` |
| Reranking | `BAAI/bge-reranker-base` (local CPU) | — | ✅ live (Phase 5); blended w/ RRF (α=0.4) |
| Embeddings | `bge-base-en-v1.5` (local, 768-d, CPU) | — | ✅ live (Phase 4); `sentence-transformers`/`torch` installed |

Cost discipline: cheap model for extraction, strong only for hard synthesis, local embeddings.

---

## 8. Next steps

**Phase 5 — DONE at the service layer** (all in `app/services/`, all mocked in tests): intent router + structured-first + lexical (FTS + typed-fact/entity exact match) + two-stage vector + RRF (`retrieval.py`); cross-encoder rerank blended with RRF (`reranking.py`); agentic corpus-aware synthesis with citations + `supported` flag (`synthesis.py`); document catalog / `find_documents` (`catalog.py`); conversation memory + `chat()` (`conversations.py`). Validated on a real corpus (now 23 docs).

**Phase 5 — DONE (chat HTTP surface, PR1):**
1. ✅ **Endpoints** (`app/api/conversations.py`, per `API_CONTRACTS.md`): `POST /conversations`, `POST /conversations/{id}/messages` (calls `conversations.chat`, supports `scope="document"`), `GET /conversations/{id}/messages`. Thin wrappers over the services; router wired in `main.py`.
2. ✅ **`retrieval_traces`**: `RetrievalTrace` ORM mapped (no migration — table pre-exists); `conversations.record_trace` writes one row per answered message inside `chat()`'s transaction.

**Phase 5 — DONE (the rest of the HTTP/UX surface, PR2):**
3. ✅ **SSE streaming**: `POST /conversations/{id}/messages/stream` emits `intent` → `find_documents`/`searching` → (`escalating`) → `done`. The loop is now `synthesis.synthesize_iter` (a generator yielding events); `chat_stream` forwards them then persists.
4. ✅ **Hard-synthesis escalation**: GPT-4o single-shot re-synth over the candidate pool when Flash returns `supported=false` (`_openai_resynthesize`); adopts only if grounded (`escalated` flag, `model=gpt-4o`).
5. ✅ **Eval**: `scripts/eval_synthesis.py` runs `synthesize` over the gold set → `answer_correctness` measured. *(Gold set is still the illustrative scaffold — refresh to the real corpus when convenient.)*
6. ✅ **Fuller traces**: `candidates` + enriched `retrieval_plan`/`context_sent` populated from `SynthesisResult.candidate_facts`/`plan`. (`reranked` stays null — reranking is internal to `retrieve`.)

**Ratings (pulled forward from Phase 7 for the testing UI):** `POST /messages/{id}/rating` → `answer_ratings` (`AnswerRating` ORM, no migration).

**Extraction robustness fix (PR #4, merged):** `EntityGroups` now coerces `{"name": X}` → `X` so a nested-entity shape never fails a whole doc (see §4 `extraction.py` / §9).

**Class-catalog API — DONE on branch `feat/classes-api` (PR #5, NOT yet merged; awaiting owner test):** `GET/POST/DELETE /api/v1/classes` (list w/ per-class `document_count`, create custom w/ slug derivation, delete custom — system classes immutable) + `GET /documents?class=<slug>` filter. No migration (the `Class` ORM was already mapped). **Behavior:** a new class is picked up by the **next** extraction (its `description` is the classifier signal); **existing docs are not retroactively re-classified** — re-run `scripts.reprocess` / `run_extraction` to evaluate them against a new class.

**Dev-only testing UI — uncommitted, temporary (see the header note):** `dev_ui/index.html`, served at `/dev/` in development, git-ignored. For hand-testing only; the real UI is Phase 6.

**Phase 6 — Frontend (Next.js):** Upload / Document view / Ask (the chat UX) / Ratings. The conversation model, tool/event vocabulary, and document-reference affordances are now locked in code so the frontend won't need backend rework.

---

## 9. Known follow-ups / debts

- **Error envelope inconsistency** — endpoints raise `HTTPException(detail={"code","message"})` → renders as `{"detail":{...}}`, but `API_CONTRACTS.md` specifies `{"error":{"code","message"}}`. Consistent across the app but doesn't match the contract; decide and add a global handler. *(Deliberately out of scope for the cleanup pass — owner doing API/auth later.)*
- **Background processing** is FastAPI `BackgroundTasks`, not the planned Redis worker — fine for now; revisit when volume/durability matters. The pipeline chains **OCR → extraction → embedding** in one in-process background task (synchronous under TestClient), so the whole chain runs on one worker thread; the network fan-out *within* a doc is bounded-parallel. The **bge model loads lazily on first real embedding** (~400 MB) — first upload after a restart pays that cost. **Re-drive after a crash:** `python -m scripts.reprocess` (BackgroundTasks don't survive a restart).
- **Embeddings validated on real facts** (Phase 4) — bge-base produces 768-d normalized vectors with sensible retrieval rankings (subscription-cost and NDA-term queries nailed their fact). A vague "how much did I spend" query mis-ranked a grocery-item fact above the amount — **expected**, and the reason aggregate/amount queries route to `typed_facts` SQL in Phase 5, not vector search.
- **Atomic-fact bbox is best-effort** — `_bbox_for_fact` token-overlaps the model's paraphrased fact against OCR-cache blocks on its page (≥0.5 overlap); below threshold → null. **Both native-PDF and Vision pages now carry block bboxes**, so provenance is box-level for all input types when the overlap matches (paraphrase-heavy facts may still fall back to page-only).
- **Doc-level summary = first chunk's summary** — for chunked (long) docs, `merge_results` takes the first non-empty chunk summary rather than synthesizing across chunks. Fine for now (page-1 intro is usually representative); a dedicated summary-merge pass would improve long-doc summaries.
- **Extraction validated on real docs** (Phase 3) via a throwaway smoke harness over `storage/samples/` (3 PDFs + 2 receipt JPEGs) against **live Vision + DeepSeek** — receipts/contract/reports all classified + extracted accurately; reports in `storage/samples/reports/` (gitignored). Quality is good; sparse pages (code listings, rubrics) legitimately yield no atomic facts.
- **List endpoint vs. contract** — `GET /documents` returns light `DocumentOut` items (not full cards) to avoid N+1; `API_CONTRACTS.md` shows `[DocumentCard]`. Detail returns the full card. Fine for now; batch-load if the list view needs card data.
- **Gemini wired (Phase 5)** — `gemini_api_key` now drives the agentic synthesis client in `synthesis.py` (`google-genai`, model `gemini-2.5-flash`). Intent routing is rules-based (no LLM), not Gemini.
- **Auth is dev-grade** (bearer = user UUID). Replace at the `get_current_user` seam before any real deployment; consider RLS as defence-in-depth alongside `AccountScope`. *(Owner doing security/auth deliberately later.)*
- **Eval gold set is illustrative scaffold** — `eval/gold/seed.yaml` `expected_doc_ids` are slugs; both eval scripts auto-map them to real UUIDs by token overlap. `answer_correctness` **is now measured** by `scripts/eval_synthesis.py` (runs `synthesize`); the remaining gap is that the gold set is still the old 8-query scaffold — **refresh it to the current 23-doc corpus** for meaningful numbers.
- **Adding a class doesn't re-classify existing docs** — the class-catalog API (`feat/classes-api`) makes new classes visible to the *next* extraction only. There's no bulk "re-classify the corpus against the new class" action yet; the workaround is `python -m scripts.reprocess` (re-runs extraction). A future endpoint could re-extract just the docs that might match.
- **Cold-start latency** — the first retrieval/answer after a restart loads the bge embedder **and** the bge-reranker (~30s observed); subsequent queries ~2.5–3s. Both are lazy CPU singletons. Consider a warmup call on boot, or GPU, if latency matters.
- **Reranker is brittle (`bge-reranker-base`)** — it under-scores answers buried in a clause (scored *"…yielding a gross margin of ~75-80%"* ≈0.002) and can't read terse `label: value` structured text. Mitigated by the **consensus-primary blend** (α=0.4) + the `exact`-label boost in `retrieval.py`. If you want better, try `bge-reranker-v2-m3` (larger, slower) and re-tune α.
- **`find_documents`/`search` tools rarely fire on small corpora** — by design: with ≤30 docs the corpus overview inlines the whole catalog, so the agent already has what it needs. They activate at scale (stats-only overview). Don't mistake "didn't search" for "broken".
- **Citation display** — distinct facts from the same doc+page surface as repeated citations (e.g. moodump p7 ×3). They're genuinely different facts; the API/frontend should **group citations by document** for display.
- **"What is X about?" returns front-matter** — overview/abstract queries over a long doc (e.g. the thesis) surface TOC/dedication facts. This is an *extraction* artifact (too many low-value front-matter facts) + the reranker rewarding literal "thesis includes…". Future fix: prefer the document **summary** (or unused `documents.summary_long`) for overview-intent queries.
- **Vision credentials fixed this session** — `vision.ImageAnnotatorClient()` used to rely on an ambient `GOOGLE_APPLICATION_CREDENTIALS`; now `ocr._vision_client` builds explicit creds from `config.vision_credentials_path` (falls back to ADC if the file is missing). Image OCR (the receipt JPEGs) failed before this and were re-driven.
- **No `requirements.txt`** — dependencies live only in the venv. `google-genai` was added this session via `pip install`. Consider freezing a manifest before the next environment rebuild.
- **Conversation history is a fixed window (12 turns), unsummarized** — accepted for now (the user's own refinement is the correction mechanism). Revisit with summarization if long chats need to retain earlier context.
- **GPT-4o escalation is live** — the `OPENAI_API_KEY` was rotated this session (old key had inactive billing → 429; new key verified working). Escalation is **best-effort**: if the hard model is unavailable it falls back to the honest Flash `supported=false` answer rather than failing the request (fixed after a live 429 crashed a stream mid-flight).
- **Current corpus has 2 `needs_review` docs** (`Smart Mirror deployment.docx`, `timer2 tutorial.docx`) — fully searchable, just low classification confidence. 0 `failed`. The `.pptx` sample is skipped (unsupported); the two identical `inotech slides` docx deduped to one.
