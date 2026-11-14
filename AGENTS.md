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
- `profiles/invoice_ua.py` — executable Ukrainian invoice profile and anchors.
- `profiles/act_ua.py` — the same tree for «Акт виконаних робіт».
- `profiles/__init__.py` — registry: the first lines of a document decide which profile owns it.
- `connectors/` — CRM connectors behind one interface: `uspacy_connector.py`, `bitrix24.py`, `matching.py`.
- `routing.py` — deterministic destination policy: document type and amount decide where a confirmed document goes.
- `validation.py` — deterministic field invariants.
- `uspacy.py` — Uspacy authentication, client-side company/deal matching and deal creation.
- `templates/index.html` — English upload/review/history UI.
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
- Every confirmation is routed: invoice to Payments, act to Accounting, anything
  above `DOCFLOW_APPROVAL_THRESHOLD` (default 100 000) to Approval. The
  destination and its reason go into the record, the result card and the CRM
  comment, so the route is auditable.
- OCR runs from a portable Tesseract in `.tools/ocr` (gitignored); `.env` puts it
  on `PATH`, so scans parse without root. Details in `docs/stack-and-operations.md`.

## Commands

Local service:

```bash
./dev.sh start
./dev.sh stop
./dev.sh restart
./dev.sh status
./dev.sh logs
```

Health:

```bash
curl -s http://127.0.0.1:8765/api/health
```

Regression baseline:

```bash
python3 tests/run_eval.py          # parser regression, 12 fixtures cases
python3 tests/test_dedupe.py       # duplicate suppression
python3 tests/test_impact.py       # impact report math
python3 tests/test_uspacy_match.py # CRM matching, no network
python3 tests/test_crm_enrichment.py # company card and contact mapping, no network
python3 tests/test_inbox.py        # review queue / exception list
python3 tests/test_document_types.py # profile detection and deal titles
python3 tests/test_connectors.py   # connector layer, Bitrix24 over a local fake
python3 tests/test_roadmap.py      # product path data and progress
python3 tests/test_routing.py      # destination rules and their explanations
```

An opt-in check against the real CRM (creates one test deal and one company):

```bash
set -a; . ./.env; set +a; python3 tools/check_crm.py
```

CI runs the deterministic suite above on every push to `main`/`develop`
(`.github/workflows/ci.yml`). No lint, typecheck or formatter is configured yet:
the pre-commit config intentionally has hygiene hooks only.

## Memory update protocol

After a meaningful behavior change:

1. update `memory/current-state.md`;
2. update `memory/next-steps.md` if the work order changed;
3. append to `memory/development-log.md`;
4. update `memory/error-catalog.md` if the defect class changed;
5. update `knowledge/` or `docs/` only for durable confirmed knowledge;
6. append three plain-language lines to `memory/plain-log.md`: what changed / why /
   what it changes for the owner. A change without these lines is not explained.

## Repository workflow

- Commit locally after every notable step; that is expected and does not need permission.
- Push to `origin` in batches (a finished feature or when the owner asks), not after
  every commit: a push is a published milestone, not a save point.
- CI is not triggered by pushes: `.github/workflows/ci.yml` runs on
  `workflow_dispatch` and on pull requests only. Run it when a batch is ready:

```bash
gh workflow run CI
```
