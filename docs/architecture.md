# Architecture

## Components

```text
Browser UI
templates/index.html
       │
       ▼
FastAPI orchestration
app.py
       ├── text acquisition → extractor.py
       ├── interpretation → extractor.py + profiles/invoice_ua.py
       ├── deterministic checks → validation.py
       ├── pending state → data/pending/<doc_id>.json
       ├── human review → templates/index.html
       ├── confirmed history → data/confirmed.jsonl
       └── CRM integration → uspacy.py
```

## Responsibilities

### `app.py`

HTTP API, orchestration, file persistence, review workflow, statistics, automation-impact report and CSV export.

### `extractor.py`

Text acquisition, OCR fallback, interpretation of Ukrainian invoice fields and confidence adjustment after validation failures.

### `profiles/invoice_ua.py`

Executable document-type knowledge: labels, synonyms, anchors, currency markers,
required fields and VAT rate. Also declares the optional requisites (address,
phone, email, bank, IBAN, contact person) and which fields are core for the
overall confidence score.

### `profiles/`

A registry of document types. The parser detects the type from the first lines
and takes every type-specific anchor from that profile, so a new document type
is a new profile module, not another branch in the extractor. Two profiles exist:
Ukrainian invoice and Ukrainian act of completed work.

### `connectors/`

One interface for every CRM: `existing_deal`, `ensure_company`, `ensure_contact`
and `create_deal`. `uspacy_connector.py` wraps the Uspacy JWT client,
`bitrix24.py` posts to a Bitrix24 incoming webhook, `matching.py` holds the
matching rules both share (tax ID by digits, names without quotes and legal form,
invoice number as a whole token). `DOCFLOW_CRM` selects the active connector.

### `validation.py`

Deterministic field invariants. Reports issues without guessing corrected values.

### `uspacy.py`

Uspacy authentication, token refresh, entity matching, company-card enrichment,
contact creation and deal creation. The
entity endpoints ignore `search`/`filter`/`limit`, so companies and deals are
read page by page and matched on the client: tax ID from `comments`, normalised
name, invoice number as a whole token. A lookup that cannot be matched exactly
returns `None` instead of an arbitrary record, and the caller creates a new
company rather than linking to a wrong one. A confirmed document fills only the
empty fields of the company card and creates the contact person once. Demo mode
short-circuits this module entirely, so a demonstration never writes to the
account.

### Review queue

`/api/inbox` lists documents that were uploaded but not confirmed. Confirming
leaves the pending snapshot on disk, so the confirmation history is what
separates "waiting for review" from "already saved". The UI renders this as the
"Needs attention" block: filename, supplier, amount and the number of tripped
invariants, and clicking a row loads the document back into review.

### `templates/index.html`

Upload, preview, editable fields, confidence, validation issues, confirmation, history and statistics.

## Runtime

1. Upload a document, choose a sample, or drop it into `data/inbox/`.
2. Extract text, detect the document type and pick its profile.
3. Parse invoice and items.
4. Validate and adjust confidence.
5. Persist pending result.
6. Review and edit in UI.
7. Confirm and persist history.
8. Create Uspacy deal if configured.

## R&D

1. Collect failed or disputed cases.
2. Classify root cause and risk.
3. Define invariant and adjacent variants.
4. Add fixture or automated check.
5. Implement bounded fix.
6. Run regression baseline and check old scenarios.
7. Update knowledge, memory and next steps.

## Source of truth map

| Concern | Source |
|---|---|
| Agent entry point | `AGENTS.md` |
| Product and domain | `knowledge/` |
| Transferable AI-engineering experience | `knowledge/ai-engineering.md` |
| Process as-is / to-be | `docs/process-map.md` |
| Case study structure | `docs/case-study.md` |
| Customer demo walkthrough | `docs/demo-script.md` |
| Current operational state | `memory/current-state.md` |
| Planned work | `memory/next-steps.md` |
| Decisions | `memory/decision-log.md` |
| Defect classes | `memory/error-catalog.md` |
| Change history | `memory/development-log.md` |
| Executable expectations | `tests/` |
| Runtime state | `data/` |
