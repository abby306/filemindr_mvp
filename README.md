# Filemindr

**Intelligent document archivist.** Drop in PDFs, images, Word docs, or email attachments — Filemindr OCRs them, extracts structured metadata (type, key facts, people, dates), and lets you search and ask questions over your whole archive with cited sources.

> v1 goal: core intelligence + retrieval engine with high accuracy. Every answer cites a source. No cross-account data leakage. Built to run on a budget (local embeddings, cheap extraction tier).

---

## What it does

```
Upload (PDF/image/docx/email)
  → OCR (PyMuPDF text layer → Google Vision fallback)
  → Structured extraction (type, facts, entities, dates) — one cheap LLM pass
  → Vector + FTS index (bge-base-en-v1.5, 768-dim, local, CPU)
  → Chat Q&A with citations over the whole archive
```

Each document goes through a four-stage pipeline: `received → ocr_done → extracted → indexed`. Every stage is append-only logged in `processing_events`; debugging is a SELECT, not a re-run.

---

## Architecture overview

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.12) |
| Database | PostgreSQL 16 + pgvector 0.8 |
| Cache / queue | Redis 7 |
| Embeddings | `bge-base-en-v1.5` — local, 768-dim, CPU, zero per-token cost |
| OCR | PyMuPDF (text-layer probe) + Google Vision (fallback / images) |
| Extraction | GPT-4o-mini / DeepSeek (cheap structured-output pass) |
| Synthesis | GPT-4o (grounded answers, low-confidence re-checks) |
| Frontend | Next.js + Tailwind + Radix UI *(planned)* |

Full design docs live in [`agent_guide/`](agent_guide/) — start with [`ARCHITECTURE.md`](agent_guide/ARCHITECTURE.md) and [`TECH_SPEC.md`](agent_guide/TECH_SPEC.md).

---

## Project structure

```
filemindr/
├── app/
│   ├── api/            # HTTP route handlers (built per phase)
│   ├── core/
│   │   ├── config.py         # pydantic-settings (DATABASE_URL, keys, etc.)
│   │   ├── auth.py           # get_current_user (bearer token → User)
│   │   ├── scoping.py        # AccountScope + get_current_account
│   │   └── default_classes.py  # 14 predefined document classes
│   ├── db/
│   │   ├── models.py   # SQLAlchemy ORM mapped to existing schema (no create_all)
│   │   └── session.py  # engine + get_db dependency
│   ├── services/       # OCR, extraction, retrieval, LLM clients (planned)
│   ├── workers/        # Background jobs via Redis (planned)
│   └── main.py         # App entry point, /health, /api/v1/me
├── alembic/            # Migrations — schema source of truth
│   └── versions/0001_initial_schema.py
├── scripts/
│   └── seed.py         # Idempotent: dev user, 2 accounts, default classes
├── tests/              # 19 passing tests (config, DB, scoping, routes)
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
            openai google-cloud-vision pymupdf python-docx pytest
```

### 4. Environment

```bash
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY (and DEEPSEEK_API_KEY / GOOGLE_APPLICATION_CREDENTIALS
# if you want those providers). DATABASE_URL and REDIS_URL are pre-filled for the local setup.
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
# 19 passed in ~0.5s (against the live local DB)
```

Tests cover: config loading + overrides, DB connectivity + pgvector presence, account isolation (scoping refuses cross-account reads), and route-level auth/403 gating. All fixtures roll back or cascade-delete after themselves.

---

## Build phases

| Phase | Status | Scope |
|---|---|---|
| **1 — Foundations** | ✅ Done | Config, ORM, session, auth, scoping, /health, seed |
| **2 — Ingest** | Planned | Upload endpoint (PDF/image/docx), hash dedup, `received` status |
| **3 — OCR** | Planned | PyMuPDF probe → Google Vision fallback; OCR cache |
| **4 — Extraction** | Planned | Structured LLM pass → card + atomic facts + embeddings → `indexed` |
| **5 — Retrieval** | Planned | Intent router, metadata/FTS/vector lanes, reranker, grounded synthesis |
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
| [`setup.md`](agent_guide/setup.md) | Machine + environment reference |

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
