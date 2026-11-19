# Architecture

## Components

```text
Browser UI
templates/index.html
       │
       ▼
FastAPI routers
routes/pages.py · routes/documents.py · routes/intake.py · routes/reports.py · routes/ops.py
       │
       ▼
services behind them
settings.py · docstore.py · intake.py · crm.py · impact.py · pgapi.py
       ├── e-mail intake → mailbox.py + data/inbox/
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

The application object and nothing else: it starts the folder watcher, the mailbox
poller and the job worker, mounts the routers, and mounts the optional PostgreSQL
layer when its driver is installed. Startup and shutdown live here because they
belong to the process, not to a route.

### `routes/`

One module per audience: `pages.py` (showcase and review workspace),
`documents.py` (upload, queue, confirmation, original file), `intake.py` (watched
folder and mailbox), `reports.py` (headline numbers, impact, CSV, config)
and `ops.py` (health, wiring, metrics). A route decides who is asking — a signed-in
person or the file-based demo — and delegates; the work itself is in the services
below.

### `settings.py`

Every knob in one place: paths, the upload and page limits, the document-id
shape, the manual baseline for the impact report and the demo switch. They are read
at the moment they are used, so redirecting storage for a test is one assignment.

### `docstore.py`

The standalone file store: pending snapshots and the confirmed history, review
time derived from the stored timestamps for legacy records, and the counts the
metrics endpoint reports. A signed-in person never reads it — their documents are
rows — but every report has a file-store path so the demo runs without a database.

### `intake.py`

The three doors into the pipeline — upload, watched folder, mailbox — end in one
function that extracts, interprets and stores a pending document, so a file that
arrives by e-mail is treated exactly like a dropped one.

### `crm.py`

What a confirmed document becomes in the CRM: the deal title, the comment block,
the supplier card, the contact, and duplicate suppression (history first, then the
provider). The connector only knows its API; the wording lives here.

### `impact.py`

The automation-impact report: auto-pass rate, field accuracy against the fields
the profile asks for, measured review time, time saved and invariant risk. Every
number derives from confirmed records; the only assumption is the stated manual
baseline, and it is returned to the caller.

### `pgapi.py`

The optional PostgreSQL layer: one import decides whether the database modules
are available, and the routes ask a single object whether a signed-in person is
present. Without the driver the demo starts and answers from files.

### `extractor.py`

Text acquisition, OCR fallback, interpretation of document fields with the anchors and value formats of the active profile, and confidence adjustment after validation failures.

### `mailbox.py`

IMAP polling: finds unseen messages, decodes their attachments, hands each file to the application (which ingests it through the same path as an upload) and marks the message seen only after the ingest succeeded. Polling state (seen Message-IDs, counters, last error) is persisted in `data/mailbox_state.json`.

### `profiles/market_ua.py`

The shared facts of the Ukrainian market: currency and VAT vocabulary,
label synonyms, tax-ID/IBAN/phone formats, legal forms for CRM matching, OCR
language, the characters allowed in a document number, and the field sets that
decide whether a document was understood. A profile imports them instead of
copying them; a second market gets its own module.

### `profiles/invoice_ua.py`, `act_ua.py`, `delivery_ua.py`

Executable document-type knowledge: title markers, anchors, total labels and the
passport the reviewer sees (UI and source labels). The core never hardcodes a
market — it asks the registry for these facts.

### `profiles/`

A registry of document types. The parser detects the type from the first lines
and takes every type-specific anchor from that profile, so a new document type
is a new profile module, not another branch in the extractor. The registry also
answers market questions: UI labels, default currency, OCR language union, legal
forms and number characters. Three profiles exist — a Ukrainian invoice, act of
completed work and delivery note — as the first implemented types; their shared
market facts live in `profiles/market_ua.py`, and a second market is another
market module plus profiles, not a rewrite.

### `connectors/`

One interface for every CRM: `existing_deal`, `ensure_company`, `ensure_contact`
and `create_deal`. `uspacy_connector.py` wraps the Uspacy JWT client,
`bitrix24.py` posts to a Bitrix24 incoming webhook, `matching.py` holds the
matching rules both share (tax ID by digits, names without quotes and legal form,
invoice number as a whole token). `DOCFLOW_CRM` selects the active connector.

### `validation.py`

Deterministic field invariants. Reports issues without guessing corrected values.
The value formats it checks (tax ID, IBAN, currencies, VAT rate) come from the
document profile, so the same invariants serve another market once its profile
is registered.

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

## Routing

`routing.py` answers one question: where does a confirmed document go. The rule
is deterministic — document type first, then an amount threshold for approval —
and it always returns a human-readable reason. The destination is stored in the
record, shown on the result card and in the history, and written into the CRM
comment, so the route can be audited later. Routing is policy: it lives outside
the profiles and outside the connectors.

## The outbox to the CRM

A confirmation and the CRM call are one business fact, but not one request: the CRM can
time out, and a person should not wait for it. `pg_queue.confirm_document` therefore writes
the row, the deal and a `crm.deal.push` job in the same transaction; `crm_outbox.push` runs
in the worker and owns everything after the commit.

The deal row is the state machine: `pending` (queued), `sending` (a worker is talking to
the CRM), `created`, `timeout` (retrying after no answer), `error` (attempts spent, needs a
person). A worker that dies mid-call leaves `sending` behind, so a retry asks the connector
`existing_deal` first — a lost answer must not become a second deal. The job itself is
retried by the queue with backoff, and `jobqueue.reap_stale` returns jobs whose worker died
to the queue while keeping the attempt count.

## Runtime

1. Upload a document, choose a sample, drop it into `data/inbox/`, or let an e-mail attachment arrive through the mailbox watcher.
2. Extract text, detect the document type and pick its profile.
3. Parse invoice and items.
4. Validate and adjust confidence.
5. Persist pending result.
6. Review and edit in UI.
7. Confirm and persist history.
8. Create Uspacy deal if configured.

## Engineering approach

**Deterministic core, one human checkpoint.** Reading a document is a judgement;
validating, routing and writing to the CRM is arithmetic and rules. The pipeline keeps
those apart: profiles interpret, validation and routing execute, the reviewer confirms.
The human is a stage, not an exception handler, and the confirmation is the only moment
a document can reach the CRM.

**Why no orchestration framework.** There is no LangGraph, CrewAI or multi-agent graph.
An agent orchestrator in the hot path would add cost, latency, non-reproducible output
and a second source of confident errors, while the formal rules are exactly what code is
better at. Async is used only where the system waits (folder watcher, IMAP poll via
`asyncio.to_thread`); document processing is sequential, so one document cannot race
itself into two deals. If a model is ever added, it proposes a value into the same
review step — it never decides on its own.

**Profiles are data.** A document type and its market (labels, value formats, legal
forms, OCR language) are a profile module; the parser and the CRM connectors are
generic. A new type is a new profile plus fixtures, not a new branch in the core.

**Error classes are the unit of work.** A defect is fixed by root cause → invariant →
test → fix → regression, not by patching one example. `docs/` records the class; `tests/` is the executable expectation.

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
| Entry point and engineering rules | `AGENTS.md` |
| Product overview | `README.md` |
| Process as-is / to-be | `docs/process-map.md` |
| Case-study structure | `docs/case-study.md` |
| Architecture and decisions | `docs/architecture.md` |
| Executable expectations | `tests/` |
