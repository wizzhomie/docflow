# Process Map — an invoice from arrival to a CRM deal

This document describes the whole process, not only the part DocFlow covers. It
serves two purposes: show the client where money is actually lost, and state
explicitly where the line runs between "automated" and "left to the human".

Diagrams: [as-is](process-as-is.svg) and [to-be](process-to-be.svg).

## 1. As-is: how it works without automation

Trigger: a supplier sends an invoice — by e-mail, in a messenger, sometimes on
paper. From then on the document lives in the correspondence and in the
operator's head.

| Step | Who | What happens | Where the risk is |
| --- | --- | --- | --- |
| 1. Invoice arrives | manager / back office | E-mail, attachment, photo | The document is lost; there is no single register |
| 2. Entry into the CRM | manager | By hand: number, date, supplier, amount, tax ID | Typos in the amount and the tax ID, suppliers mixed up |
| 3. Check | accountant | Compares amount, VAT and bank details by eye | A look-over with no rules and no trace |
| 4. Approval | manager in charge | Correspondence, verbal agreements | No history of decisions |
| 5. Payment | accountant | 1C / bank | Duplicates and late payments surface late |

Where the process loses money:

- **Entry errors.** Amount, VAT and tax ID are typed by hand; the mismatch
  surfaces at payment or later.
- **Duplicates.** The same invoice sent twice creates a second deal; in the CRM
  that is two records for one document.
- **No audit.** Nobody can say who changed the amount, when, and on what basis.
- **No measurement.** Volume, time and error rate are unknown — nothing to
  improve.

Cost: **3–10 minutes per document**, plus the price of an error, which surfaces
in finance rather than in the CRM.

## 2. To-be: the process after automation

The trigger is the same, but the document lands in the system instead of in the
correspondence.

1. **Intake** — upload in the interface, an API call or a mailbox; the file is
   stored unchanged.
2. **Text extraction** — digital PDF, or OCR for scans; how the text was obtained
   is recorded.
3. **Interpretation** — the document-type profile pulls out fields and line items.
4. **Validation** — deterministic invariants, no model; anything doubtful gets a
   low confidence.
5. **Review** — the operator sees only what is doubtful; edits it, confirms it.
6. **CRM** — a deal with amount, currency, comment and a link to the company by
   tax ID.
7. **History and metrics** — what was extracted, what the human changed, how long
   it took.

Roles shift: the operator stops being a data-entry clerk and becomes an arbiter
of exceptions. The process owner gets metrics. Nobody "just types data in".

### Control points

| Check | What it catches | What the system does |
| --- | --- | --- |
| Date in ISO format | an unreadable date | confidence down, the document is flagged |
| Tax ID format | junk in the company details | confidence down |
| Total ≥ 0 and numeric | a swapped amount | confidence down |
| VAT = 0 or ≈ 20/120 of the total | wrong VAT | confidence down |
| Currency ∈ {UAH, USD, EUR} | a wrong currency | confidence down |
| Σ line items ≈ total | an invoice that does not add up | priority review |
| Duplicate by number + tax ID | a repeated submission | no deal is created; the existing one is shown |

The principle: a check **reports and lowers trust**, but never silently
"corrects" a value — otherwise an error becomes a confident error.

### Exceptions and where they go

| Exception | What the process does |
| --- | --- |
| Text not extracted (bad scan) | the document is flagged, manual entry; not "silence" |
| An invariant failed | priority review, the operator has to look |
| Duplicate invoice | the repeat is blocked and the existing deal is shown |
| CRM unavailable | the error is stored on the record, the send is retried by hand or by the worker |
| Unknown supplier | a company is created from the name and tax ID |

An exception with no clear owner and no trace is the main reason automation
"never took off" at other vendors.

### Process metrics

| Metric | Meaning | Where in DocFlow |
| --- | --- | --- |
| auto-pass rate | share of documents confirmed without edits | `GET /api/impact` |
| median review time | median "upload → confirmation" | `GET /api/impact` |
| corrections by field | where the human actually intervenes | `GET /api/impact` |
| duplicates blocked | how many repeats never reached the CRM | `GET /api/impact` |
| risky confirmations | confirmed despite a failed invariant | `GET /api/impact` |
| time and cost per document | before/after against the manual baseline | `DOCFLOW_MANUAL_MINUTES` |

## 3. The automation boundary

- The machine reads, interprets and checks — and **stops** where there is no
  reliable signal of correctness.
- The human confirms; confirmation is a normal stage, not a failure.
- Forbidden: sending an amount, a tax ID or a currency to the CRM when the system
  is not sure of it. Better to stop and show it to a human.

## 4. What of the to-be is already built

An honest gap map — this is what a client should be shown.

| Stage | Status | Comment |
| --- | --- | --- |
| Upload intake | done | PDF, images, text, samples |
| API / mailbox intake | not done | the only channel is the web |
| Text extraction + OCR | partial | OCR runs locally, but not inside the Docker image |
| Field interpretation | done | Ukrainian invoice profile; a supplier sometimes carries the tax ID in its name |
| Invariant validation | done | 7 rules, with a regression set |
| Review and edits | done | English interface, confidence and issues |
| CRM deal | done | amount, currency, comment, company by tax ID |
| Deduplication | partial | the pre-create check exists but needs work |
| History and metrics | done | `confirmed.jsonl`, `/api/impact`, CSV |
| Exception queue | not done | exceptions are visible, but there is no queue or SLA |
| Retries when the CRM is down | partial | a retry worker exists, the behaviour is thinly covered |

## 5. Assumptions a pilot has to test

- The manual baseline (3 minutes) is an assumption, not a measurement. A pilot
  measures it on real people and real documents.
- We treat a document confirmed without edits as correct. That is only checkable
  by sampling audits.
- Synthetic invoices do not prove generalisation; a real sample is mandatory.
