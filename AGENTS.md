# AGENTS.md — DocFlow

## Project

DocFlow extracts fields from documents, sends them through human review, and creates
CRM deals (Uspacy or Bitrix24). The service is market-neutral: a document profile carries
everything that belongs to one market — field labels, default currency, OCR language,
tax-ID and IBAN formats, legal forms for CRM matching. Three profiles ship today: a
Ukrainian invoice, an act of completed work and a delivery note. Adding a document type
or a second market is a new profile, not a rewrite of the core. UI in English.

The pipeline is deterministic on purpose: profiles interpret, code validates and routes,
a human confirms. There is no LLM or agent framework in the decision path.

## Architecture map

- `app.py` — the application object: startup work, mounted routers, optional PostgreSQL layer.
- `routes/` — HTTP groups (pages, documents, intake, reports, ops); `settings.py` holds
  every knob, `docstore.py` the file store, `intake.py` the three intake doors, `crm.py`
  the CRM mapping and duplicate suppression, `impact.py` the impact report, `pgapi.py`
  the optional database layer.
- `extractor.py` — text acquisition, OCR fallback and field interpretation.
- `mailbox.py` — IMAP intake: attachments of new mail go through the same pipeline as an upload.
- `profiles/market_ua.py` — shared facts of the Ukrainian market: currency, VAT, label
  vocabulary, tax-ID/IBAN/phone formats, legal forms, OCR language.
- `profiles/invoice_ua.py` — executable Ukrainian invoice profile: anchors, totals wording,
  number context and the labels the reviewer sees.
- `profiles/act_ua.py` — the same tree for an act of completed work.
- `profiles/delivery_ua.py` — the same tree for a delivery note.
- `profiles/__init__.py` — registry: the first lines of a document decide which profile
  owns it; the core asks the registry for labels, currency, OCR languages, legal forms and
  number characters instead of hardcoding one market.
- `connectors/` — CRM connectors behind one interface: `uspacy_connector.py`, `bitrix24.py`,
  `matching.py`.
- `routing.py` — deterministic destination policy: document type and amount decide where a
  confirmed document goes.
- `validation.py` — deterministic field invariants.
- `uspacy.py` — Uspacy authentication, client-side company/deal matching and deal creation.
- `templates/landing.html` — showcase page at `/` (router scene plus live stats).
- `templates/index.html` — review workspace at `/app`.
- `tests/run_eval.py` — deterministic regression baseline.
- `tests/document_cases.json` — labelled fixtures for every document type.
- `docs/roadmap.json` + `/api/roadmap` — the product path shown on the site.

## Engineering rules

1. Separate probabilistic interpretation from deterministic execution.
2. Keep whole-document context; line-by-line parsing causes confident errors.
3. Confident errors are worse than missing values. Never silently send a wrong amount,
   supplier, tax ID or currency to CRM.
4. Preserve uncertainty. Human review is expected, not a failure.
5. Fix defect classes, not single examples: root cause → invariant → test → fix → regression.
6. Keep runtime processing and the system-improvement loop separate.
7. Prioritize by consequences, not only frequency.
8. Prefer deterministic code for formal rules. Avoid default heavy LLM and multi-agent runs.
9. Extend document-type profiles and supplier templates instead of growing an unbounded
   regex pile.
10. Do not read, disclose or commit secrets and runtime data.

## Demo mode

- `DOCFLOW_DEMO_MODE=1` — Confirm returns a simulated deal and never calls the CRM.
- `DOCFLOW_DEMO_MODE=0` switches the same Confirm to the configured CRM account; a
  confirmed document creates the supplier company and a deal with amount, VAT and tax ID.
- Uspacy ignores `search`/`filter`/`limit` on entity endpoints: read pages and match on the
  client. A 200 answer does not prove the filter was applied.
- A confirmed document fills the supplier company card (phone, e-mail, address, tax ID,
  bank, IBAN in comments), creates or reuses the contact and links both to the deal. Empty
  company fields are filled; values already present are never overwritten.
- `python3 tools/seed_demo.py [--reset]` seeds the history for the impact report.
- `python3 tools/make_extra_samples.py`, `tools/make_pilot_batch.py` and
  `tools/make_delivery_sample.py` regenerate the sample documents used for the walkthrough.
- `DOCFLOW_CRM=uspacy|bitrix24|none` picks the active connector; Bitrix24 works through an
  incoming webhook (`BITRIX24_WEBHOOK_URL`).
- Documents dropped into `data/inbox/` are picked up by a background watcher.
- `DOCFLOW_IMAP_*` connects one mailbox: attachments are ingested through the same
  pipeline, a message is marked seen only after a successful ingest, and seen Message-IDs
  are persisted in `data/mailbox_state.json`.
- Every confirmation is routed: invoice to Payments, act and delivery note to Accounting,
  anything above `DOCFLOW_APPROVAL_THRESHOLD` to Approval. The destination and its reason
  go into the record, the result card and the CRM comment.
- OCR runs from a portable Tesseract in `.tools/ocr` (gitignored); the language set is the
  union of the registered profiles and `DOCFLOW_OCR_LANGS` overrides it.

## Commands

```bash
./dev.sh start
./dev.sh stop
./dev.sh restart
./dev.sh status
./dev.sh logs
curl -s http://127.0.0.1:8765/api/health
```

Regression baseline:

```bash
python3 tests/run_eval.py            # 13 fixtures, 104/104 checks, 0 confident errors
python3 tests/run_eval_split.py      # real vs synthetic split
pytest -q --cov --cov-fail-under=70  # the whole suite, one process, coverage gate
```

CI runs the same suites with the coverage gate, the lint and a Docker build on every push
to `main` and on pull requests (`.github/workflows/ci.yml`).

## Working notes

Internal working memory (`memory/`), market knowledge (`knowledge/`) and operations
runbooks are part of the development environment but are not published: they are
git-ignored. The public tree is the product: code, profiles, tests, documentation and
sample documents.
