# Filemindr

**Intelligent document archivist.** Drop in PDFs, images, Word docs, or email attachments — Filemindr OCRs them, extracts structured metadata (type, key facts, people, dates), and lets you search and ask questions over your whole archive with cited sources.

> v1 goal: core intelligence + retrieval engine with high accuracy. Every answer cites a source. No cross-account data leakage. Built to run on a budget (local embeddings, cheap extraction tier).

---

## What it does

```
Upload (PDF/image/docx/email)
  → OCR (PyMuPDF text layer → Google Vision fallback)
  → Structured extraction (type, facts, entities, dates) — one cheap LLM pass (DeepSeek)
  → Vector + FTS index (bge-base-en-v1.5, 768-dim, local, CPU)
  → Hybrid retrieval (structured + lexical + vector, fused with RRF) → cross-encoder rerank
  → Agentic chat Q&A with citations (Gemini 2.5 Flash; GPT-4o escalation on hard misses)
```

Each document goes through a four-stage pipeline: `received → ocr_done → extracted → indexed`. Every stage is append-only logged in `processing_events`; debugging is a SELECT, not a re-run.

---

## Architecture overview

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.12) |
| Database | PostgreSQL 16 + pgvector 0.8 |
| Cache / queue | Redis 7 *(installed; background work currently via FastAPI `BackgroundTasks`)* |
| Embeddings | `bge-base-en-v1.5` — local, 768-dim, CPU, zero per-token cost |
| Reranking | `bge-reranker-base` — local cross-encoder, CPU, blended with RRF |
| OCR | PyMuPDF (text-layer probe) + Google Vision (fallback / images) |
| Extraction | DeepSeek `deepseek-chat` (cheap structured-output pass) |
| Synthesis | Gemini 2.5 Flash (agentic, grounded, cited); GPT-4o escalation on `supported=false` |
| Frontend | Next.js + Tailwind + Radix UI *(planned)*; a throwaway dev testing UI ships at `/dev/` |

Full design docs live in [`agent_guide/`](agent_guide/) — start with [`ARCHITECTURE.md`](agent_guide/ARCHITECTURE.md) and [`TECH_SPEC.md`](agent_guide/TECH_SPEC.md).

---

## Project structure

```
filemindr/
├── app/
│   ├── api/            # HTTP routes: documents.py, conversations.py (chat/SSE/ratings), schemas.py
│   ├── core/
│   │   ├── config.py         # pydantic-settings (DATABASE_URL, keys, etc.)
│   │   ├── auth.py           # get_current_user (bearer token → User)
│   │   ├── scoping.py        # AccountScope + get_current_account
│   │   ├── retry.py / concurrency.py  # bounded retries + bounded network fan-out
│   │   └── default_classes.py  # 14 predefined document classes
│   ├── db/
│   │   ├── models.py   # SQLAlchemy ORM mapped to existing schema (no create_all)
│   │   └── session.py  # engine + get_db dependency
│   ├── services/       # ocr, extraction, embeddings, retrieval, reranking,
│   │                   #   synthesis, catalog, conversations, storage, events, reprocessing
│   └── main.py         # App entry point, /health, /api/v1/me, dev /dev/ UI mount
├── alembic/            # Migrations — schema source of truth
│   └── versions/       # 0001_initial_schema, 0002_summary_embedding_hnsw
├── scripts/            # seed, seed_corpus, reprocess, ask, chat, retrieve,
│                       #   eval_retrieval, eval_synthesis
├── eval/               # Retrieval/synthesis eval harness (scorers, gold set, runner)
├── tests/              # 168 passing tests (every network/model seam mocked)
├── agent_guide/        # Full design docs (PRD, ARCHITECTURE, TECH_SPEC, API_CONTRACTS…)
├── schema.sql          # Canonical DDL (applied via Alembic)
├── .env.example        # Copy to .env and fill in keys
└── docker-compose.yaml # Postgres 16 + pgvector + Redis (for later Docker phase)
```

---

## Setup (native — Ubuntu 22+/24+/26+)

### 1. Prerequisites

```bash
# PostgreSQL 16 + pgvector (PGDG)
sudo apt install -y postgresql-16 postgresql-16-pgvector

# Redis
sudo apt install -y redis-server

# pyenv (if not installed)
curl https://pyenv.run | bash
# then add pyenv init to your shell profile and restart the shell

# Python 3.12
pyenv install 3.12.13
```

### 2. Database

```bash
sudo -u postgres psql <<SQL
CREATE ROLE filemindr WITH LOGIN PASSWORD 'localdev';
CREATE DATABASE filemindr OWNER filemindr;
GRANT ALL ON DATABASE filemindr TO filemindr;
SQL

# Enable pgvector
psql "postgresql://filemindr:localdev@localhost:5432/filemindr" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 3. Python environment

```bash
cd filemindr
pyenv local 3.12.13          # pins the version via .python-version
python -m venv .venv
source .venv/bin/activate

pip install fastapi "uvicorn[standard]" sqlalchemy "psycopg[binary]" pgvector \
            alembic pydantic "pydantic-settings" python-multipart httpx \
            openai google-genai google-cloud-vision pymupdf python-docx \
            sentence-transformers langdetect pyyaml pytest
```

> No `requirements.txt` yet — dependencies live in the venv. `sentence-transformers`
> pulls in `torch` (the local bge embedder + reranker run on CPU). `openai` is reused
> for both DeepSeek (custom `base_url`) and GPT-4o; `google-genai` drives Gemini.

### 4. Environment

```bash
cp .env.example .env
# Edit .env — fill in DEEPSEEK_API_KEY (extraction), GEMINI_API_KEY (synthesis),
# OPENAI_API_KEY (GPT-4o escalation), and GOOGLE_APPLICATION_CREDENTIALS (Vision OCR).
# DATABASE_URL and REDIS_URL are pre-filled for the local setup.
```

### 5. Apply schema + seed

```bash
# Apply all migrations (schema.sql is already wired into alembic/versions/0001)
alembic upgrade head

# Seed: dev user, personal + company accounts, 14 default classes per account
python -m scripts.seed
# Output includes the bearer token UUID you'll use for local dev API calls.
```

### 6. Run

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API explorer.

**Quick smoke test:**

```bash
# Health check
curl localhost:8000/health
# → {"status":"ok","env":"development","database":"up"}

# Authenticated endpoint (replace UUID with the one printed by seed.py)
curl -H "Authorization: Bearer <user-uuid>" \
     -H "X-Account-Id: <account-uuid>" \
     localhost:8000/api/v1/me
```

**Try the full pipeline (live APIs):**

```bash
python -m scripts.seed_corpus      # ingest storage/samples/* through OCR→extraction→embedding
python -m scripts.ask "<question>" # one-shot grounded answer with citations
python -m scripts.chat             # interactive multi-turn chat with memory
```

In development, a throwaway testing UI (document list + chat + streamed steps +
thumbs/stars rating) is served at [http://localhost:8000/dev/](http://localhost:8000/dev/)
when a local `dev_ui/` directory exists (git-ignored).

---

## Tenancy & security model

Every table carries a denormalized `account_id`. **All queries go through `AccountScope`**, which enforces `WHERE account_id = :active` and raises at the call site if a model lacks `account_id` — cross-account leakage is a programming error, not a silent runtime risk.

Request auth: `Authorization: Bearer <user_id>` resolves the user; `X-Account-Id` header (or the user's sole membership) selects the active account, verified against `account_members`. Returns 401 / 403 on any failure.

> Auth is intentionally minimal in Phase 1 (dev bearer tokens). Production auth (sessions/JWT) slots in at the `get_current_user` seam in [`app/core/auth.py`](app/core/auth.py).

---

## Document classes

14 predefined system classes ship out of the box: `invoice`, `receipt`, `contract`, `id_document`, `bank_statement`, `tax_document`, `payslip`, `utility_bill`, `insurance`, `medical_record`, `report`, `letter`, `resume`, `warranty`. Users can add custom classes per account. Descriptions feed the extraction-time classifier.

---

## Tests

```bash
pytest -q
# 168 passed (against the live local DB; all network/model seams mocked)
```

Tests cover the whole pipeline offline: config/DB/pgvector, account isolation (scoping refuses cross-account reads), OCR routing, extraction parsing + fan-out, embeddings, hybrid retrieval + RRF + reranking, the agentic synthesis loop (incl. GPT-4o escalation), conversation memory, and every HTTP route (upload, chat, SSE streaming, ratings). DeepSeek, Vision, the bge encoder/reranker, and the Gemini + GPT-4o seams are all mocked — no live calls, no model downloads. All fixtures roll back or cascade-delete after themselves.

---

## Build phases

| Phase | Status | Scope |
|---|---|---|
| **1 — Foundations** | ✅ Done | Config, ORM, session, auth, scoping, /health, seed |
| **2 — Ingest** | ✅ Done | Upload endpoint (PDF/image/docx), streaming + hash dedup, `received` status |
| **3 — OCR** | ✅ Done | PyMuPDF probe → Google Vision fallback; block bboxes; OCR cache |
| **4 — Extraction** | ✅ Done | Structured LLM pass → card + atomic facts + embeddings → `indexed` |
| **5 — Retrieval + synthesis** | ✅ Done | Intent router, structured/FTS/vector lanes + RRF, reranker, agentic synthesis (Gemini + GPT-4o escalation), chat endpoints, SSE streaming, retrieval traces, ratings |
| **6 — Frontend** | Planned | Next.js: Upload, Document view, Ask, Ratings |
| **7 — Analytics / Billing** | Planned | Usage counters, subscription tiers, quota enforcement |

Full backlog: [`agent_guide/TASKS.md`](agent_guide/TASKS.md).

---

## Key design docs

All in [`agent_guide/`](agent_guide/):

| Doc | What's in it |
|---|---|
| [`PRD.md`](agent_guide/PRD.md) | Product scope, users, success criteria |
| [`ARCHITECTURE.md`](agent_guide/ARCHITECTURE.md) | System design, pipeline flows A+B |
| [`TECH_SPEC.md`](agent_guide/TECH_SPEC.md) | Schema outline, runtime models, retrieval algorithm |
| [`API_CONTRACTS.md`](agent_guide/API_CONTRACTS.md) | Endpoint shapes, request/response types |
| [`AGENTS.md`](agent_guide/AGENTS.md) | Rules for AI coding agents working in this repo |
| [`CODING_STANDARDS.md`](agent_guide/CODING_STANDARDS.md) | Conventions: style, seams, scoping, tests |
| [`setup.md`](agent_guide/setup.md) | Machine + environment reference |

A live development handoff (current state, file-by-file map) lives in [`STATUS.md`](STATUS.md).

---

## Contributing

- Read [`agent_guide/AGENTS.md`](agent_guide/AGENTS.md) before writing code.
- Schema changes go through **Alembic only** — never call `Base.metadata.create_all()`.
- Every query must be account-scoped through `AccountScope`.
- Provenance (page + bbox) must be captured from the first OCR pass — it's painful to retrofit.
- Keep docs in sync: if you change schema, endpoints, or conventions, update `TECH_SPEC.md` / `API_CONTRACTS.md` / `TASKS.md` in the same commit.

---

## License

Private — all rights reserved.
