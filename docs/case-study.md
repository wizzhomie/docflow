# Case Study — invoice processing automation (template)

A working template for a case study. Everything in square brackets is filled
with real data after the pilot; invented numbers are not allowed in this
document — the first question ("and how did you calculate that?") exposes them.

Headline in one line: **[Company] — [N] invoices a month processed without manual entry into the CRM.**

## 1. Context

- Company: [name, industry, size].
- Volume: [N] invoices a month, [M] suppliers.
- Who takes part: [manager / accountant / owner].
- What it looked like before: invoices in e-mail and messengers, entry into the CRM by hand.

## 2. The process "before"

Diagram: [process-as-is.svg](process-as-is.svg).

| Parameter | Value |
| --- | --- |
| Time per document | [T_manual, min] (measured by [source: time study / interview / timesheets]) |
| Errors per month | [E_manual] (according to [source]) |
| Duplicates per month | [D_manual] |
| Who pays for it | [role] |

What actually hurts (3–4 points, each with a price):

1. [For example: manual amount entry — 2 errors a month, each one surfaces at payment.]
2. [For example: invoices get lost in the correspondence — 1–2 a month.]
3. [For example: no edit history — a dispute with a supplier is settled from memory.]

## 3. Why it was built this way

- **The human confirms, the machine checks.** No value the system is unsure of
  reaches the CRM; anything doubtful stops at review.
- **Checks are code, not a model.** Amount, VAT, tax ID format and line-item
  totals are checked deterministically, the same way every time.
- **Document profiles instead of a "universal parser".** A new document type or
  supplier is a separate profile, not a patch in shared code.
- **Synthetic data only for edge cases.** Real documents remain the basis of the
  assessment.

Alternatives we deliberately rejected:

- A fully automatic mode without review — cheaper, but the risk of a confident
  error in the CRM.
- A universal LLM layer "for everything" — unverifiable and irreproducible.

## 4. The process "after"

Diagram: [process-to-be.svg](process-to-be.svg). Details in [process-map.md](process-map.md).

In short: intake → extraction + OCR → interpretation → invariants → review of
the doubtful only → a deal in the CRM → history and metrics.

## 5. Result

| Metric | Before | After | How it was measured |
| --- | --- | --- | --- |
| Time per document | [T_manual] | [T_review] | median "upload → confirmation", `GET /api/impact` |
| Share confirmed without edits | 0% (all by hand) | [A]% | `auto_pass_rate` |
| What gets corrected most | — | [field, N times] | `top_corrections` |
| Duplicates in the CRM | [D_manual] a month | [D_after] | `duplicate_confirmations`, CRM log |
| Documents with open issues | not tracked | [R] | `unreviewed_risk_documents` |

Run size: [N] documents, period [dates], of which real [K].

## 6. Economics

The method (mandatory, otherwise the number is fiction):

```text
hours a month before = N documents × T_manual / 60
hours a month after  = N documents × T_review / 60
saving a month       = (hours before − hours after) × hourly rate [rate, source]
```

Bottom line: [X] hours and [Y] per month; the deployment pays for itself in [Z]
months at a deployment cost of [C].

Separately — the **cost of an error**: [P(error)] × [cost of the error: penalty,
overpayment, a lost supplier].

## 7. Risks and how they are controlled

| Risk | How it is closed |
| --- | --- |
| A confident error in the amount | invariant + low confidence → review |
| A wrong tax ID | format check + matching against the company in the CRM |
| A duplicate deal | number + tax ID key, checked before creation |
| A poor scan | OCR + an explicit "text not extracted" flag |
| The CRM is unavailable | the error is stored on the record, the send is retried |
| Audit | which fields were edited and when — kept in the history |

## 8. What did not work / what I would do differently

- [An honest point: for example, we overestimated OCR quality on photos and had
  to tighten intake and require a PDF.]
- [For example: the first validation rules raised false alarms on suppliers with
  non-standard VAT.]

## 9. Next

1. [An exception queue with an SLA — so doubtful documents do not sit around.]
2. [Mailbox intake — currently web only.]
3. [Attaching the source PDF to the deal and reconciling with 1C.]

## 10. Appendix: how it was verified

- Regression: `python3 tests/run_eval.py` — [X/Y] cases, [K] confident errors.
- Report checks: `python3 tests/test_impact.py` — [N] deterministic cases.
- Metrics: `GET /api/impact` on real confirmations, period [dates].
- Stack: FastAPI, Tesseract (ukr+eng), Uspacy API, Docker, Tailscale.

## Checklist before publishing

- [ ] Every number in this document can be recomputed from the system data or from a measurement.
- [ ] It states which documents are real and which are synthetic.
- [ ] There is at least one "what did not work" point — without it the case does not read as true.
- [ ] The economics are shown with the calculation method and the assumptions.
- [ ] Diagrams are embedded as images, not left as "see appendix".
