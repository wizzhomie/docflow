# Measuring the meaning layer with qagent

The meaning layer (`meaning.py`) asks a model *where* a value is printed and drops every
pointer it cannot verify. That is the right runtime behaviour, but it leaves no number:
how often does the model point at the right line, and does the layer change an answer the
deterministic parser already produced.

`tests/eval_meaning.py` answers those questions with [`qagent`](https://github.com/wizzhomie/qagent)
around the labelled fixtures in `tests/document_cases.json`. No production code changed:
the layer still ships as it is.

## What is measured

For every fixture the script builds a qagent case (`text` in, golden field values out) and
runs two agents over the same dataset:

| Run | Agent | What it is |
|---|---|---|
| `--parser` | `parse_invoice()` only | the deterministic reader, no model |
| default | parser + model pointers, merged like `meaning.apply()` | what production would answer |

The model is asked exactly what production asks: `meaning.build_prompt()` and
`meaning.build_request()` are the same functions `_ask()` uses, so the field set
(`meaning.gaps()`) and the answer schema cannot drift from the runtime path. Three
deterministic checks run around it, with no extra model call and no extra cost:

| Code | Meaning |
|---|---|
| `M1_POINTER_TEXT` | the quoted text is not on the line the model named — the pointer would be dropped silently |
| `M2_POINTER_LINE` | the line number is outside the document |
| `M3_POINTER_REUSED` | two fields point at the same line and quote |
| `M4_POINTER_DISAGREES` | the pointer reads a different value than the parser, and production trusts the pointer |

Scoring is ground truth against the golden values with `min_field_accuracy=1.0`: a case
passes only when all seven fields are right. "Pass" therefore means "the answer that
reaches the reviewer is correct", not "the gate let it through".

## Results (13 fixtures, 2026-11-22)

| | parser alone | parser + meaning layer |
|---|---|---|
| Pass, final | 61.5% (8/13) | 61.5% (8/13) |
| False pass rate | 38.5% (5/13) | 38.5% (5/13) |
| Field accuracy (macro) | 94.5% | 94.5% |
| Fields the model was asked about | — | 1–2 of 7 per document |
| Pointers resolved | — | all of them |
| Disagreements flagged | — | 0 |
| Tokens | 0 | 5 111 prompt / 710 completion |
| Cost | $0 | $0 (Groq free tier, then the committed cassette) |
| Latency p95 | 1 ms | 7.7 ms from the cassette (live calls are bounded by the 30 s timeout) |

Three findings, in order of how much they change decisions:

1. **The layer is a no-op on these fixtures.** `gaps()` asks only about the fields the
   rules could not vouch for — on these documents that is `total`, `vat` and occasionally
   `number` — and the model resolves every pointer to the value the parser already had.
   Quality does not move in either direction; the layer buys provenance, not accuracy.
2. **The fixtures cannot show the layer at its best.** The five remaining failures are
   fields the layer was never asked about (`supplier`, `currency`, dates) or totals the
   model agreed with. The code's own comment says the rules are wrong about `total` on 4
   of 12 real ProZorro invoices; that is the population where the layer can earn `on`,
   and it is not in this dataset.
3. **The checks are the durable part.** `M1`–`M3` explain a drop `resolve()` performs
   silently, and `M4` turns "the pointer won" into a counted event. On this dataset they
   found nothing — which is a result worth keeping in the report rather than a reason to
   remove them.

An earlier run of the harness before `gaps()` existed asked all seven fields and showed
the risk plainly: on `ocr-noise` a pointer overrode a correct parser value for `number`,
and `M4_POINTER_DISAGREES` was the only thing that made it visible. Narrowing the question
to the gaps is what removed that failure mode — the experiment is why the harness is worth
keeping.

## What follows

- `DOCFLOW_MEANING=shadow` stays the honest setting for this profile set: no quality gain
  is demonstrated, so `on` would be a preference, not a measurement.
- To give the layer a fair trial, add the real invoices where `total` and `vat` are known
  to be hard (the corpus the fine-tune work already collected) and run the same harness.
  The script needs no changes for that: fixtures are data.
- Keep `M4` in shadow: it is the check that would catch a pointer replacing an approved
  value, and it costs nothing.

## Running it

```bash
python3 tests/eval_meaning.py --parser          # free baseline, no model
MEANING_REPLAY=1 python3 tests/eval_meaning.py  # the committed cassette: free, offline
MEANING_RECORD=1 python3 tests/eval_meaning.py  # live call; fills gaps in the cassette
```

The script imports qagent from `~/qagent/src`; point `QAGENT_SRC` elsewhere if the library
lives in another checkout. `MEANING_EVAL_BASE_URL` and `MEANING_EVAL_MODEL` override the
endpoint (Groq by default, the same provider the demo's meaning layer uses). Reports land
in `.run/meaning/` (git-ignored): JSON + Markdown per mode plus `parser_vs_meaning.md`. The
cassette, `tests/meaning_cassette.jsonl`, is committed so the numbers are reproducible
without a key.
