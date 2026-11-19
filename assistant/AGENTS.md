# AGENTS.md — DocFlow Operator

## Project

An assistant over DocFlow. The user asks in plain language; a small local model
(Qwen2.5-1.5B-Instruct Q4_K_M via `llama.cpp`, CPU only) plans and calls a fixed set of tools
that read the DocFlow review queue, check duplicates, prepare letters and build a daily
summary. Deterministic FAQ and intent layers answer what they can before the model is asked.
The assistant never confirms a document, sends mail or moves money by itself.

It is part of the DocFlow product, in the same repository (`assistant/`), but runs as a
separate service. It uses the DocFlow API at `:8765` as a tool; it does not edit the
application's files.

## Engineering rules

1. Deterministic layers first: a curated FAQ and intent rules answer what they can; the model
   plans only what is left. Typed tools execute. No free-form side effects: every action is a
   named tool with a validated schema.
2. Human gate for money and confirmation. The agent proposes; the person confirms in
   DocFlow.
3. Read before write: v1 tools are read-only plus local draft files.
4. A wrong tool call must fail loudly (schema, allowlist, trace), never silently.
5. Constrained decoding: tool calls are validated against a JSON schema/grammar.
6. Deterministic fallback: the same demo scenarios must also run on a rules-based plan, so
   the agent can be compared with a baseline.
7. Evaluate, don't claim: twelve scripted tasks, success rate and tool validity, recorded.
8. Do not read, disclose or commit secrets, runtime data or model files.
9. Every change ends with `scripts/ci.sh` green: ruff, offline tests and the coverage gate
   (90%). "Works" means the command passed, not that the code was written.
10. Tests fake DocFlow, llama-server and model decisions. A test that needs the real
    services is marked `live` and must be skipped when they are down.
11. Deploy only through `scripts/deploy.sh` (laptop) or `../deploy/release.sh` (server),
    and check it with `status` plus one real question — never by "the process is running".

## Stack

- Python 3.12 + FastAPI for the operator server and UI; plain HTML/JS.
- `llama.cpp` (`llama-server`) with a GGUF model, CPU only.
- Drafts are files and traces are JSONL (`data/`); there is no database.
- DocFlow API (`http://127.0.0.1:8765`) as the only external dependency.
- Tests: pytest with fakes; ruff and coverage configured in `pyproject.toml`.

Ports: DocFlow `8765`, this assistant `8769`, its model `8080`.

## Tests, CI and deploy

- `scripts/ci.sh` — lint, offline tests, coverage gate (90%, currently 98%). Green before done.
- `scripts/ci.sh --live` — the opt-in live smoke (real DocFlow, model, `/api/chat`).
- `scripts/deploy.sh deploy|status|restart|stop|logs` — the laptop/container path: a plain
  process supervisor, pids and logs in `.run/` (git-ignored).
- On the demo server systemd owns the services: units live in `../deploy/systemd`
  (`docflow-operator.service`, `docflow-operator-model.service`), installed once with
  `sudo ../deploy/systemd/install.sh` and released with `sudo ../deploy/release.sh`.
- `.github/workflows/ci.yml` mirrors lint, coverage and an API boot smoke on GitHub.

## Working with the product owner

- Russian, informal "ты", short: 1–3 sentences first, then details.
- No code, logs or file lists in chat; explain through consequences.
- One question at a time, with options and a recommendation.
- A big task starts with a 2–4 line plan and a wait for "да".
- No installs or downloads without an explicit yes (the owner approved `llama.cpp` + one
  model for this project; that approval covers the initial setup only).
- Never claim "works" before a real check: the model must answer, a tool call must execute,
  the trace must be visible.
- End a completed piece with 2–3 lines: done / verified / remaining.

## Memory protocol

Internal notes live in `memory/` and are git-ignored (same policy as DocFlow's public tree):
`handoff.md` (state and next steps), `decision-log.md`, `development-log.md`,
`plain-log.md`. After a meaningful change update them and commit locally.

## Repository workflow

- Commit locally after every notable step; push only when the owner asks.
- Part of the DocFlow repository: this project has no repository of its own. Commits go
  into the checkout that contains it, and the root CI runs `assistant/` as one job.
