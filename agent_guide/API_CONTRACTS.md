# API_CONTRACTS.md — filemindr

Interface contracts for the v1 API. **Draft** — shapes will firm up as endpoints are built. Add/adjust fields at a low level; keep the overall shape stable.

## Conventions
- Base path `/api/v1`. JSON in/out. Auth required (mechanism TBD); the authenticated request resolves a **user** + **active account**.
- All resources are implicitly scoped to the active `account_id`; it is never passed by the client for scoping.
- IDs are UUID strings. Timestamps are ISO-8601 UTC.
- Errors: `{ "error": { "code": str, "message": str } }` with appropriate HTTP status.

## Documents

### POST /documents (upload)
Multipart file upload. Returns the created document in `received` state.
- Req: `multipart/form-data` — `file` (pdf/png/jpg/docx).
- Res `201`: `{ id, status, original_filename, mime_type, byte_size, created_at }`
- Dedup: identical `(account, file_hash)` returns the existing document (`200`).

### GET /documents
List documents (paginated, filterable).
- Query: `status?`, `class?`, `q?` (text), `limit?`, `cursor?`
- Res `200`: `{ items: [DocumentCard], next_cursor }`

### GET /documents/{id}
- Res `200`: full `DocumentCard` (see schema below).

### DELETE /documents/{id}
- Res `204`.

## Classes

### GET /classes — list predefined + custom classes.
### POST /classes — create custom class `{ name, description }` → `201`.
### DELETE /classes/{id} — remove a custom class (system classes immutable).

## Chat / query

### POST /conversations → `{ id }`
### POST /conversations/{id}/messages
Send a user message; get a grounded answer.
- Req: `{ content, scope?: "account"|"document", document_id? }`
- Res `200`:
```json
{
  "message_id": "uuid",
  "answer": "text",
  "citations": [
    { "document_id": "uuid", "title": "…", "page": 3, "fact_id": "uuid" }
  ],
  "supported": true
}
```
- `supported=false` ⇒ answer states the documents don't contain it; `citations` may be empty.

### GET /conversations/{id}/messages — message history.

## Email-in (webhook)

### POST /ingest/email
Inbound email handler (provider webhook). Resolves account by recipient alias, ingests attachments + body as documents.
- Auth: provider signature / shared secret (not user auth).
- Res `200` on accept.

## Ratings

### POST /messages/{id}/rating
Attach feedback to an answer.
- Req: `{ rating: "up"|"down", stars?: 1-5, reasons?: ["not_grounded"|"missing_doc"|"wrong_number"|"wrong_document"], comment?: string }`
- Res `200`: `{ ok: true }`. Writes `answer_ratings` linked to the message's retrieval trace.

## Analytics

### GET /analytics/usage
- Query: `range?` (e.g. `30d`)
- Res `200`: `{ documents, queries, storage_bytes, token_spend, series: { documents_over_time: [...], queries_per_day: [...] }, top_classes: [...], most_asked_documents: [...] }`

### GET /analytics/quality
- Res `200`: `{ answer_rating_pct, grounded_pct, avg_retrieval_ms, extraction_success_pct }`

## Billing

### GET /billing/plans → list `plans` with limits.
### GET /billing/subscription
- Res `200`: `{ plan, status, period_end, usage: { documents, queries, storage_bytes }, limits: {...} }`
### POST /billing/checkout
- Req: `{ plan_slug }` → Res `200`: `{ checkout_url }` (provider-hosted).
### GET /billing/invoices → `{ items: [Invoice] }`.

> Quota: write paths (`POST /documents`, message creation) check `usage_counters` against plan limits and return `402`/`429` with an upgrade hint when exceeded.

### DocumentCard
```json
{
  "id": "uuid",
  "status": "received|ocr_done|extracted|indexed|failed|needs_review",
  "title": "string",
  "summary": "string",
  "language": "en",
  "page_count": 4,
  "classes": [{ "slug": "invoice", "confidence": 0.97 }],
  "entities": { "people": [], "organizations": [], "places": [] },
  "dates": [{ "value": "2025-04-01", "role": "due" }],
  "typed_facts": [{ "label": "invoice_total", "value": "1240", "value_numeric": 1240, "type": "money", "unit": "USD" }],
  "created_at": "iso-8601"
}
```

## Not in v1 (placeholders)
- PDF compilation, smart collections, share links, voice endpoints, RBAC/permission fields.