# The machine API

The platform that owns a document flow can talk to DocFlow without a browser. An
integration uploads a document, reads the fields the pipeline extracted — with the
confidence each value was read with and the issues the rules raised — and either
polls until its own reviewer approves the document or confirms it through the API.
Confirming through the API is not a shortcut around review: it runs the same
invariants the review form runs, and the record names the key that approved it.

The door is keyed, versioned and off by a switch. It is the same extraction, the
same parser and the same confirmation the workspace calls; an integration cannot
reach an answer that a person could not.

## Authentication

A key is issued by the operator and printed once:

```bash
python3 tools/api_key.py issue --label "Platform ERP" --scopes documents:write,documents:read
```

```
id:      3f2a91c4d0b7
label:   Platform ERP
scopes:  documents:read, documents:write
company: file store

dfk_3f2a91c4d0b7_9c1e... (48 hex characters)
```

The token is sent in a header, never in a URL:

```bash
curl -s http://127.0.0.1:8765/api/v1/me -H "Authorization: Bearer dfk_..."
curl -s http://127.0.0.1:8765/api/v1/me -H "X-API-Key: dfk_..."      # the same key
```

`data/api_keys.json` holds the id, the label, the scopes and the SHA-256 of the
secret — never the secret. A copy of that file cannot be replayed as a key; a lost
token is replaced, not recovered:

```bash
python3 tools/api_key.py list
python3 tools/api_key.py revoke 3f2a91c4d0b7
```

### Scopes

| Scope | What it allows |
|---|---|
| `documents:write` | upload a document (`POST /documents`, `POST /documents/text`) |
| `documents:read` | read the queue and one document (`GET /documents`, `GET /documents/{id}`) |
| `documents:confirm` | record an approval (`POST /documents/{id}/confirm`) |

`GET /api/v1/me` answers with no scope: a client checks its own key before it starts.

A missing, unknown or revoked key is `401` with a `WWW-Authenticate: Bearer` header.
A key without the scope a route needs is `403` — a reader cannot confirm.

## Endpoints

### `POST /api/v1/documents`

Multipart, one file. `external_id` is optional and makes the call idempotent.

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/documents \
  -H "Authorization: Bearer dfk_..." \
  -F "file=@invoice.pdf" \
  -F "external_id=ERP-2026-000123"
```

```json
{
  "id": "7d3c1f0a94",
  "status": "pending",
  "external_id": "ERP-2026-000123",
  "filename": "invoice.pdf",
  "uploaded_at": "2026-02-12T10:31:07+02:00",
  "doc_type": "invoice_ua",
  "doc_type_label": "Invoice",
  "fields": {
    "number": {"value": "API-17", "confidence": 0.92},
    "total": {"value": 1234.56, "confidence": 0.9},
    "tax_id": {"value": "41234567", "confidence": 0.88}
  },
  "values": {"number": "API-17", "total": 1234.56, "tax_id": "41234567"},
  "issues": [],
  "confidence": 0.91,
  "route": {"destination": "Payments", "code": "payments", "reason": "invoice"},
  "confirmed_at": null
}
```

`values` is a convenience for callers that do not care about confidence; the same
number lives in `fields`. A value the parser could not read is absent, not empty.

### `POST /api/v1/documents/text`

A document the platform has already turned into text: no OCR, the same parser.

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/documents/text \
  -H "Authorization: Bearer dfk_..." -H "Content-Type: application/json" \
  -d '{"text": "Рахунок на оплату № 17 …", "filename": "invoice.txt", "external_id": "ERP-18"}'
```

### `GET /api/v1/documents`

The queue of this key's documents, newest first. `status=pending` (default) or
`status=confirmed`; `limit` is capped at 100.

```bash
curl -s "http://127.0.0.1:8765/api/v1/documents?status=confirmed&limit=5" \
  -H "Authorization: Bearer dfk_..."
```

### `GET /api/v1/documents/{id}`

One document, with its current status. A document uploaded through the API is
readable only by the key that uploaded it (`404` otherwise); on PostgreSQL the
tenant of the key decides.

### `POST /api/v1/documents/{id}/confirm`

Records the approval the platform already made and delivers the document exactly
as the review form's Confirm does: routing, duplicate check, CRM deal.

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/documents/7d3c1f0a94/confirm \
  -H "Authorization: Bearer dfk_..." -H "Content-Type: application/json" \
  -d '{"fields": {"total": 1234.56}}'
```

- An empty body (`{}`) confirms exactly what was extracted.
- A supplied value that differs from the extraction goes through the same
  invariants as the form: a bad tax id or an amount that does not match the items
  is `422`, with the field labels a reviewer sees. A platform cannot confirm a
  value its own form would have refused.
- A second confirmation of the same document is `409`.
- The record in the history carries `confirmed_by: "api:<key id>"`, so an approval
  made through the API is attributable and appears in the impact report like any
  other.

## Idempotency

`external_id` is unique per key. The same `external_id` again answers `200` with
the document that already exists and `"idempotent": true`, instead of a second
queue entry. The content is not compared: an integration that changes the file
under the same id must use a new id — otherwise the first answer is the answer.

## Tenancy

| Key | Where its documents live |
|---|---|
| issued without `--company` | the file store; the key is the tenant |
| issued with `--company <id>` | the PostgreSQL row of that company; the company's queue and history |

A company-bound key requires the database layer: on an instance without it the
answer is `503`, not a document written into a shared folder. Documents uploaded by
a key appear in the workspace queue the operator reviews — except on the public
showcase (`DOCFLOW_DEMO_MODE=1`), which lists the labelled demo and the visitor's
own uploads, never an integration's.

## Limits and errors

| Status | When |
|---|---|
| `400` | a format the pipeline does not read (supported: PDF, TXT, HTML, PNG, JPG, TIFF) |
| `401` | no key, unknown key, revoked key |
| `403` | the key lacks the scope |
| `404` | no such document / the door is switched off (`DOCFLOW_API=off`) |
| `409` | the document is already confirmed |
| `413` | larger than `DOCFLOW_MAX_UPLOAD_MB` (default 20) or more pages than `DOCFLOW_MAX_PDF_PAGES` |
| `422` | an invariant refused a submitted value, an empty document, a bad `external_id` |
| `503` | the extraction queue is busy (`Retry-After: 5`), or a company key without a database |

## What the API does not do

- **No webhook yet.** Poll `GET /api/v1/documents?status=confirmed`. A notification
  that can be lost is worse than a poll: the platform would need the poll anyway.
- **No per-key rate limit.** The extraction gate is the real bound: two documents
  at a time, and the rest get `503` with `Retry-After` instead of a growing queue.
- **No `route` on `GET /documents/{id}` for PostgreSQL documents.** The destination
  depends on the company's approval threshold, which that route does not read; the
  field is `null` there, while the pending list and the workspace queue carry it.
- **No confirmed timestamp for PostgreSQL documents** in `GET /documents/{id}`: the
  history (`status=confirmed`) has it.

## Security notes

- The key travels in a header only. Nothing in this application puts it in a URL,
  which is what keeps it out of access logs and browser history.
- The file keeps digests; the comparison is constant time.
- Revoke a key the moment an integration is retired: `python3 tools/api_key.py revoke <id>`.
- A confirm scope is a real right: issue `documents:read` to a reporting integration
  and it will never be able to approve anything.
