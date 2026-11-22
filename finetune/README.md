# The pointer model, fine-tuned here

The meaning layer (`meaning.py`) asks a model one narrow question: *on which line is
this value printed?* The model answers with a line number and the exact substring; the
code checks the substring is on that line, copies it, parses it and revalidates the
result. This directory is how that model was made: the corpus generator, the training
script, the A/B harness that measures it through the real intake path, and the numbers
those runs produced.

The shipped adapter is a **QLoRA fine-tune of Qwen3-1.7B**, trained on one 6 GB consumer
GPU (peak 4.21 GB), 238 examples, one epoch, 30 optimizer steps, 66 MB as a GGUF.
Inference is `llama.cpp` on CPU — no provider, no key, no document leaving the machine.

```
finetune/
  corpus/make_text_corpus.py     generate the synthetic corpus and its pointer labels
  corpus/make_training_data.py   corpus → chat JSONL in exactly the runtime prompt
  train_qlora.py                 QLoRA fine-tune (4-bit NF4, LoRA r=16, loss on the answer)
  eval_pipeline.py               A/B through intake: rules alone vs rules + the layer
  latency_probe.py               one real request, measured: tokens and wall time
  results/                       what the runs above produced
```

## Why a 1.7B model, and why pointing

Three constraints decided it. The card has 6 GB and 5.0 GiB of that is free; the target
server is an ARM VM with two cores and no accelerator; and a reviewer waits on this step.
A 7B model does not fit the first, is unusable on the second and misses the third. The
task itself is small — pick a line out of sixty and quote it — so capacity is not the
bottleneck; format discipline is. That is what a narrow fine-tune buys: the model emits
the pointer JSON instead of prose about the document, which is what it does when the
request carries no JSON schema — three of twelve real invoices came back as prose in one
measured run, and the whole request was lost.

Pointing rather than answering is what keeps the model from being a source of confident
errors. It cannot invent an amount, because the amount is copied from the document by
the code, and the pointer is dropped if the text is not there.

## The data

`corpus/make_text_corpus.py` generates Ukrainian invoices, acts and delivery notes across
several layouts, with the labels the model needs: for every field, the line number and
the printed substring. Text, not PDFs, because `extract_text()` is what the layer
receives — the corpus exercises exactly the input the model will see.

`corpus/make_training_data.py` turns each document into one chat record whose user
message is built by the same `meaning.PROMPT` the runtime uses, numbered lines included.
The assistant target is the pointer JSON. The loss is computed on the answer only: the
prompt is masked with `-100`, so the model learns where to point, not to reproduce a
long Ukrainian instruction. A marker that drifts from the runtime prompt is worse than
none, which is why the generator imports the prompt instead of copying it.

Split is deterministic and stratified by layout: 238 train, 31 validation, 30 held-out,
no document in two splits. The shipped adapter saw synthetic text only; the real
invoices below were never in training.

## Training

| | |
| --- | --- |
| base | `Qwen/Qwen3-1.7B`, 4-bit NF4, double quantisation |
| adapter | LoRA r=16, alpha=32, dropout 0.05, all linear layers |
| schedule | 1 epoch, lr 1e-4 cosine, grad accumulation 8, seq 1024 |
| cost | 30 steps, peak 4.21 GB VRAM, minutes on a 1660 Ti |
| artefact | 66 MB adapter as GGUF, loaded next to the quantised base |

`results/loss.json` is the trainer's own log: 0.43 at step 5, 0.0018 at step 15, 0.0005
at step 30. That collapse is worth stating plainly — it says the model fits 238 examples,
not that it reads invoices. The evidence for the second claim is the next section.

### A run that was thrown away

One intermediate adapter was trained only on the cases a bug had broken — 120 documents
with the buyer's tax id printed before the supplier's — plus 40 replay documents. It
scored 216 of 217 on the synthetic corpus and then answered a real invoice with
`GGGG…` instead of JSON: narrow fine-tuning cost the model its output format. It is not
shipped. The adapter here is the full-corpus one, which gives up a point on the synthetic
set and keeps the format everywhere.

The lesson went into the pipeline rather than the notebook: the request now carries a
JSON schema, and a server that rejects the schema gets the plain request instead
(`meaning._ask`), so a format failure degrades to the rules rather than to gibberish.

## What it actually bought

`eval_pipeline.py` runs the documents through `intake._process` twice, with
`DOCFLOW_MEANING=off` and `on`, and scores every field as correct, wrong, missing or
**confidently wrong** — the category that matters, because a confident wrong amount
closes the wrong deal.

Held-out synthetic corpus, 30 documents, 210 fields:

| | rules | rules + layer |
| --- | ---: | ---: |
| correct fields | 178 | **207** |
| confidently wrong | 11 | **0** |

That second row is the one that decided the design. On `vat` alone the rules were
confidently wrong eleven times in thirty documents, which is the failure mode a review
queue cannot catch — a plausible number with a high weight. The layer answered all thirty
`vat` lines correctly and left three `total` fields wrong, all flagged, none confident.

Real Ukrainian invoices published by ProZorro, 12 documents, 84 fields, never in
training:

| | rules | rules + layer |
| --- | ---: | ---: |
| correct fields | 64 | **74** |
| `date` | 12/12 | 12/12 |
| `vat` | 2/12 | **9/12** |
| `total` | 4/12 | **6/12** |
| median time per document, warm | — | **10.9 s** |

Two caveats belong here rather than in a footnote. The rules row is measured *after* two
parser defects found by this experiment were fixed (worded dates, a supplier tax id that
was the buyer's), so the layer's own contribution is the money columns, not the date
column. And 84 fields is a small sample: a difference of one or two fields is noise on
this corpus, which is why the unit of the decision was the *class* of error — the rules
read `vat` wrongly on 10 of the 12 invoices, and after the layer they read it wrongly on
3.

## The cost, and why the model step is not in the response

On the two cores of the target VM the model generates 5.6–5.9 tokens/s; the full
seven-field request took ~27 s, so asking for everything was not deployable. Two changes
followed, and both are in the repository rather than in this directory:

* ask only about the gaps — the fields the rules left missing, refused or below 0.6
  confidence, plus `total` and `vat` always, because the rules are confidently wrong
  there (`meaning.gaps`);
* take the model step out of the upload response and run it right after it
  (`intake.finish_meaning`, scheduled by the route): the person gets the rules' answer in
  the normal request time, and the model's findings land in the record a few seconds
  later, through the same invariants.

That is the 10.9 s median in the table: a background step, not a wait.

## Running it

Training needs a CUDA GPU and `torch transformers peft trl bitsandbytes datasets
accelerate`; evaluation needs the model server. From the repository root:

```bash
python finetune/corpus/make_text_corpus.py --count 300 --out data/synthetic-corpus-300
python finetune/corpus/make_training_data.py --corpus data/synthetic-corpus-300 --out finetune/data/training
python finetune/train_qlora.py --epochs 1
# GGUF for llama.cpp: python convert_lora_to_gguf.py finetune/data/adapter-qwen3-1.7b
```

Then a server and the A/B:

```bash
llama-server -m Qwen3-1.7B-Q4_K_M.gguf --lora adapter-qwen3-1.7b.gguf \
  -c 4096 -t 2 -tb 2 --jinja --port 8080
DOCFLOW_MEANING_TIMEOUT=300 python finetune/eval_pipeline.py
```

`eval_pipeline.py` defaults to the held-out synthetic corpus and writes
`finetune/data/pipeline-eval.json`; `results/pipeline-eval-synthetic.json` in this
directory is the output of that command.

Nothing here is run by the product, so `tests/test_finetune.py` generates a corpus and
builds the training set on every CI run: the prompt it checks is `meaning.PROMPT` itself,
so a change to the runtime request that the generator does not follow fails the build
instead of a retrain. The `finetune` job in `.github/workflows/ci.yml` lints this
directory by path and runs those five tests. Pointing the layer at a local server needs no
key — `DOCFLOW_MEANING_URL=http://127.0.0.1:8080/v1/chat/completions`,
`DOCFLOW_MEANING_MODEL=local`, and nothing leaves the machine.

## What is not in the repository

* **Weights.** The adapter is 66 MB; the trained files stay out of git. `train_qlora.py`
  plus the corpus generator reproduces them.
* **The real invoices.** They are third-party documents, so the 84-field table cannot be
  re-run from this repository. `eval_pipeline.py` runs on any directory of texts with a
  manifest of expected values, which is how the table was made.
* **The scanned half.** Scans were rendered and OCR'd during the experiment; their
  numbers (the adapter reads OCR text better than the rules do, and the rules' tax-ID
  anchor is where OCR hurts most) are not shipped, because the pages are real invoices.
* **The retrain this experiment asked for.** The shipped adapter was trained before the
  `Поля:` line was added to the prompt (the gap-only request), so it is used slightly out
  of distribution. The numbers above were measured with the current prompt, so the effect
  is bounded by them — but regenerating the data with today's prompt and retraining is
  the honest next step, not a claim that it is done.
