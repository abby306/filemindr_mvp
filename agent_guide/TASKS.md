# TASKS.md — filemindr

Live backlog. Move items between sections; keep entries one line. `[ ]` todo, `[~]` in progress, `[x]` done.

## Done
- [x] Dev environment: Ubuntu 26.04, pyenv 3.12, project scaffold.
- [x] Native PostgreSQL 16 + pgvector (PGDG); role/db `filemindr`; extension enabled.
- [x] Native Redis.
- [x] Core Python libraries installed (FastAPI, SQLAlchemy, psycopg, pgvector, alembic, openai, google-cloud-vision, pymupdf, python-docx).
- [x] `setup.md` + project docs (AGENTS/PRD/ARCHITECTURE/TECH_SPEC/TASKS/CODING_STANDARDS/API_CONTRACTS).

## In progress
- [~] Apply baseline schema (Alembic `0001` / `schema.sql`) at `vector(768)`; verify in DBeaver.

## Done (recent)
- [x] Lock embedding model → `bge-base-en-v1.5`, `vector(768)`; install `sentence-transformers`.

## Next up
- [ ] Schema as Alembic migration: identity/tenancy + documents/card + vector + observability tables + `v_document_pipeline`.
- [ ] Account scoping at data layer (RLS or mandatory scoping function).
- [ ] FastAPI skeleton: settings (pydantic-settings), DB session, health endpoint.

## Backlog — by build phase
**Ingest**
- [ ] Web upload endpoint (PDF/PNG/JPG/docx); persist raw file; hash dedup; `received` status.
- [ ] Email-in pipeline (per-account alias; parse attachments + body).

**OCR**
- [ ] PDF text-layer probe (fitz) → Google Vision fallback; docx extract; image → Vision.
- [ ] OCR cache keyed by file hash; language detection; keep bboxes.

**Extraction**
- [ ] Structured-output schema + single cheap LLM pass → card + atomic facts.
- [ ] Write card tables; store `extraction_raw`; confidence routing to `needs_review`.
- [ ] Embed atomic facts + summary (local model); `indexed` status.

**Retrieval**
- [ ] Metadata/SQL layer; FTS/BM25; two-stage vector; reranker; intent router.
- [ ] Account-scoped throughout.

**Synthesis**
- [ ] Grounded answers with citations; "unsupported" path; write `retrieval_traces`.

**Quality**
- [ ] Eval harness: gold queries, recall@k, answer correctness; run on every retrieval/prompt change.

**Frontend (design system in `FRONTEND.md` / design PDF)**
- [ ] Design tokens → Tailwind/CSS vars (light + dark); Inter + Geist Mono; base components on Radix.
- [ ] Upload screen: dropzone + optimistic cards + live pipeline fill.
- [ ] Document view: card, classification + confidence, typed facts with provenance jump, add/label classes.
- [ ] Ask screen: streaming answer + retrieval trace reveal + click-to-source citations + scope toggle.
- [ ] Ratings: thumb + diagnostic reasons on each answer.
- [ ] Framer Motion: the 3 signature motions; honor reduced-motion.

**Analytics & billing (fast-follow)**
- [ ] `usage_events` + `usage_counters`; analytics endpoints (usage + quality).
- [ ] Analytics page: usage + quality dashboards (Recharts/Visx).
- [ ] Plans/subscriptions/invoices tables; billing endpoints; checkout (provider-hosted).
- [ ] Billing page: plan card, usage meters, pricing cards, invoices.
- [ ] Quota enforcement on write paths (402/429 + upgrade hint).

**Later (post-v1)**
- [ ] Voice agent, WhatsApp ingest, PDF compilation, smart collections, expiry reminders, share links.
- [ ] Dockerize + deploy to Contabo.
