# AGENTS.md — DocFlow

## Project

DocFlow extracts fields from Ukrainian invoices and acts, sends them through human review, and creates CRM deals (Uspacy or Bitrix24). Target market: Ukraine; documents in Ukrainian; amounts in UAH; UI in English.

## Mandatory context

Before planning, implementing or reviewing a change, read:

- `memory/handoff.md` — start here after a context reset: state, commands, next steps;
- `docs/plain-guide.md` — the same system in plain language for the product owner;
- `memory/plain-log.md` — three-line explanations of every noticeable change;
- `memory/current-state.md` — confirmed implementation and limitations;
- `knowledge/architecture-principles.md` — non-negotiable engineering principles;
- `memory/next-steps.md` — agreed work order;
- `memory/decision-log.md` — decisions that must not be silently reversed.

Load additional context only when relevant:

- `knowledge/product.md` and `knowledge/domain.md`;
- `knowledge/ai-engineering.md` for transferable AI-engineering judgment;
- `memory/error-catalog.md` for defect work;
- `docs/architecture.md` for component boundaries;
- `docs/process-map.md` for the as-is / to-be process and exception handling;
- `docs/stack-and-operations.md` for commands and dependencies;
- `memory/development-log.md` for history.

## Architecture map

- `app.py` — FastAPI orchestration, persistence, review workflow and reporting.
- `extractor.py` — text acquisition, OCR fallback and field interpretation.
- `mailbox.py` — IMAP intake: attachments of new mail go through the same pipeline as an upload.
- `profiles/invoice_ua.py` — executable Ukrainian invoice profile and anchors.
- `profiles/act_ua.py` — the same tree for «Акт виконаних робіт».
- `profiles/__init__.py` — registry: the first lines of a document decide which profile owns it.
- `connectors/` — CRM connectors behind one interface: `uspacy_connector.py`, `bitrix24.py`, `matching.py`.
- `routing.py` — deterministic destination policy: document type and amount decide where a confirmed document goes.
- `validation.py` — deterministic field invariants.
- `uspacy.py` — Uspacy authentication, client-side company/deal matching and deal creation.
- `templates/landing.html` — English showcase page served at `/`: the looping router scene
  (intake → read → human review → route → CRM) plus live stats from `/api/stats` and `/api/impact`.
- `templates/index.html` — English review workspace (upload/review/history UI), served at `/app`.
- `tests/run_eval.py` — deterministic regression baseline.
- `tests/document_cases.json` — compact labelled fixtures for every document type (invoice, act).
- `docs/roadmap.json` + `/api/roadmap` — the product path shown on the site: stages with a decision each.
- `app.py` serves the review queue: `/api/inbox` lists uploaded-but-unconfirmed
  documents, so a document survives a page reload until someone decides on it.

## Working with the product owner

The owner is not a programmer and pays for understanding, not for throughput.
Prefer fewer, explained changes: a long push of features without explanation is
technical debt in his head, even when the code is correct.

## Engineering rules

1. Separate probabilistic interpretation from deterministic execution.
2. Keep whole-document context; line-by-line parsing causes confident errors.
3. Confident errors are worse than missing values. Never silently send a wrong amount, supplier, tax ID or currency to CRM.
4. Preserve uncertainty. Human review is expected, not a failure.
5. Fix defect classes, not single examples: root cause → invariant → test → fix → regression.
6. Keep runtime processing and the system-improvement loop separate.
7. Prioritize by consequences, not only frequency.
8. Prefer deterministic code for formal rules. Avoid default heavy LLM and multi-agent runs.
9. Extend document-type profiles and supplier templates instead of growing an unbounded regex pile.
10. Do not read, disclose or commit secrets and runtime data.

## Demo mode

- `DOCFLOW_DEMO_MODE=1` in `.env` — Confirm returns a simulated deal and never
  calls Uspacy; the UI shows a `DEMO · CRM simulated` badge.
- `DOCFLOW_DEMO_MODE=0` switches the same Confirm to the real account
  (`USPACY_BASE_URL`, currently `aitesting.uspacy.com`); a confirmed document
  then creates the supplier company and a deal with amount, VAT and tax ID.
- Uspacy ignores `search`/`filter`/`limit` on entity endpoints: read pages and
  match on the client. A 200 answer does not prove the filter was applied.
- A confirmed document fills the supplier company card (phone, email, address,
  tax ID, bank, IBAN in comments), creates or reuses the contacted person and
  links both the company and the contact to the deal. Empty company fields are
  filled; values already present are never overwritten.
- `python3 tools/seed_demo.py [--reset]` fills `data/confirmed.jsonl` with
  labelled `source: synthetic` rows for the impact report.
- `python3 tools/make_extra_samples.py` regenerates the extra sample invoices
  (inline tax id, Russian language, image-only scan).
- `python3 tools/make_pilot_batch.py` regenerates the four `pilot_*` invoices used
  for a hands-on walkthrough: new numbers, dates and tax IDs, so a first upload
  goes through the normal confirm path instead of hitting duplicate protection.
- `DOCFLOW_CRM=uspacy|bitrix24|none` picks the active connector. Bitrix24 works
  through an incoming webhook: `BITRIX24_WEBHOOK_URL=https://<portal>/rest/1/<token>/`.
- Documents dropped into `data/inbox/` are picked up by a background watcher and
  appear in the review queue without a manual upload.
- `DOCFLOW_IMAP_*` in `.env` connects one mailbox: new attachments are ingested
  through the same pipeline (`source: mailbox`), the message is marked seen only
  after a successful ingest, and seen Message-IDs are persisted in
  `data/mailbox_state.json`. Empty settings disable the mailbox. Verify with
  `GET /api/mailbox` and `python3 tests/test_mailbox.py`.
- Every confirmation is routed: invoice to Payments, act to Accounting, anything
  above `DOCFLOW_APPROVAL_THRESHOLD` (default 100 000) to Approval. The
  destination and its reason go into the record, the result card and the CRM
  comment, so the route is auditable.
- OCR runs from a portable Tesseract in `.tools/ocr` (gitignored); `.env` puts it
  on `PATH`, so scans parse without root. Details in `docs/stack-and-operations.md`.
