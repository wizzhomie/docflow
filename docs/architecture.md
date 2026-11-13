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

Executable document-type knowledge: labels, synonyms, anchors, currency markers, required fields and VAT rate.

### `validation.py`

Deterministic field invariants. Reports issues without guessing corrected values.

### `uspacy.py`

Uspacy authentication, token refresh and deal creation.

### `templates/index.html`

Upload, preview, editable fields, confidence, validation issues, confirmation, history and statistics.

## Runtime

1. Upload document or choose sample.
2. Extract text.
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
