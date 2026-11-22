#!/usr/bin/env python3
"""Offline regression for the meaning layer, measured by qagent.

The meaning layer answers "where is this value printed" and the code drops every pointer
it cannot verify — silently, by design. That leaves two questions no log answers today:
how often does the model point at the wrong line, and does the layer add anything over the
deterministic parser? This script answers both with numbers instead of impressions.

What it does:

- turns the 14 labelled cases in ``tests/document_cases.json`` into a qagent dataset;
  the golden values are the same ones ``tests/run_eval.py`` already trusts;
- runs the meaning call through qagent, with a check per pointer (line exists, the quoted
  text is really on that line, two fields do not share one pointer) and a score against
  the golden values;
- runs the parser alone as a second report, and diffs the two.

Sources, by environment:

- no flags       a live call to the same endpoint the demo uses (Groq);
- ``MEANING_RECORD=1`` the same call, and every answer is appended to
  ``tests/meaning_cassette.jsonl``;
- ``MEANING_REPLAY=1`` the committed cassette: no key, no network, no bill.

Usage::

    python tests/eval_meaning.py --parser          # no model, free
    MEANING_RECORD=1 python tests/eval_meaning.py  # once, writes the cassette
    MEANING_REPLAY=1 python tests/eval_meaning.py  # afterwards, free
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QAGENT_SRC = Path(os.environ.get("QAGENT_SRC", Path.home() / "qagent" / "src")).expanduser()
for extra in (ROOT, QAGENT_SRC):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from pydantic import BaseModel, ConfigDict  # noqa: E402
from qagent import (  # noqa: E402
    AgentReply,
    Case,
    CheckContext,
    Criterion,
    EvalCase,
    FieldSpec,
    Issue,
    JudgePolicy,
    LLMRequest,
    Message,
    Preset,
    Pricing,
    QAConfig,
    ReplayClient,
    RetryPolicy,
    Rubric,
    ScoringSpec,
    compare,
    evaluate,
    report,
    usage_of,
)
from qagent.evals.compare import write_markdown as write_diff  # noqa: E402

import meaning  # noqa: E402  the project module under test
from extractor import parse_invoice, parse_items  # noqa: E402

CASES = Path(__file__).with_name("document_cases.json")
CASSETTE = Path(__file__).with_name("meaning_cassette.jsonl")
OUT = ROOT / ".run" / "meaning"
BASE_URL = os.environ.get(
    "MEANING_EVAL_BASE_URL", "https://api.groq.com/openai/v1"
).removesuffix("/chat/completions")
MODEL = os.environ.get("MEANING_EVAL_MODEL", "qwen/qwen3.8-27b")
MAX_TOKENS = 700
PRICING = Pricing()  # Groq's demo key is on the provider's free tier; the report says so


class Pointer(BaseModel):
    """One claim by the model: this value is printed on this line, as this text."""

    model_config = ConfigDict(extra="ignore")

    line: int
    text: str


class MeaningAnswer(BaseModel):
    """What the QA layer sees: the pointers, and the three readings of a value.

    ``parser_values`` is the deterministic reader alone, ``pointer_values`` is what the
    model's pointers yield, and ``values`` is what production would use: the pointer wins
    where it resolved, the parser fills the rest (``meaning.apply``).
    """

    model_config = ConfigDict(extra="ignore")

    pointers: dict[str, Pointer | None]
    parser_values: dict[str, str | float | None]
    pointer_values: dict[str, str | float | None]
    values: dict[str, str | float | None]


class PointerCheck:
    """Turn the silent drop inside ``meaning.resolve`` into named, countable issues."""

    id = "meaning-pointer"
    tier = "fast"

    def __call__(self, output: MeaningAnswer, ctx: CheckContext) -> list[Issue]:
        lines = str(ctx.case.payload).splitlines()
        issues: list[Issue] = []
        seen: dict[tuple[int, str], str] = {}
        for name, pointer in output.pointers.items():
            if pointer is None:
                continue
            span = pointer.text.strip()
            key = (pointer.line, span.casefold())
            if not 1 <= pointer.line <= len(lines):
                issues.append(
                    Issue(
                        code="M2_POINTER_LINE",
                        severity="error",
                        source="check",
                        field_path=f"fields.{name}.line",
                        message=f"line {pointer.line} is outside a {len(lines)}-line document",
                    )
                )
                continue
            if span.casefold() not in lines[pointer.line - 1].casefold():
                issues.append(
                    Issue(
                        code="M1_POINTER_TEXT",
                        severity="error",
                        source="check",
                        field_path=f"fields.{name}.text",
                        message=(
                            f"the quote for {name!r} is not on the line it names; "
                            "the pointer would have been dropped silently"
                        ),
                    )
                )
                continue
            if key in seen:
                issues.append(
                    Issue(
                        code="M3_POINTER_REUSED",
                        severity="warning",
                        source="check",
                        field_path=f"fields.{name}",
                        message=f"the same line and quote are already used for {seen[key]!r}",
                    )
                )
            else:
                seen[key] = name
        for name, pointer_value in output.pointer_values.items():
            parser_value = output.parser_values.get(name)
            if pointer_value is None or parser_value is None:
                continue
            if not meaning._agrees(name, parser_value, pointer_value):
                issues.append(
                    Issue(
                        code="M4_POINTER_DISAGREES",
                        severity="warning",
                        source="check",
                        field_path=f"pointer_values.{name}",
                        message=(
                            f"the pointer reads {pointer_value!r} where the parser reads "
                            f"{parser_value!r}; production trusts the pointer"
                        ),
                    )
                )
        return issues


RUBRIC = Rubric(
    criteria=[
        Criterion(
            id="grounding",
            title="Grounding",
            description="Every pointer names a line that exists and text that is printed there.",
            weight=1.0,
        )
    ],
    pass_score=0.9,
)

SCORING = ScoringSpec(
    fields=[
        FieldSpec(path="values.number", comparator="normalized"),
        FieldSpec(path="values.date", comparator="exact"),
        FieldSpec(path="values.supplier", comparator="normalized"),
        FieldSpec(path="values.tax_id", comparator="normalized"),
        FieldSpec(path="values.total", comparator="numeric", tolerance=0.01),
        FieldSpec(path="values.vat", comparator="numeric", tolerance=0.01),
        FieldSpec(path="values.currency", comparator="exact"),
    ],
    min_field_accuracy=1.0,
)

PRESET = Preset(
    name="docflow-meaning",
    output_model=MeaningAnswer,
    rubric=RUBRIC,
    checks=(PointerCheck(),),
    scoring=SCORING,
    agent_model=MODEL,
)


def _answer_for(
    plan: dict, text: str, parsed: dict, asked: list[str] | None = None
) -> MeaningAnswer:
    """Resolve the pointers and merge them the way production does, for scoring."""
    resolved = meaning.resolve(plan, text, fields=asked)
    raw_fields = (plan.get("fields") or {}) if isinstance(plan, dict) else {}
    pointers: dict[str, Pointer | None] = {}
    for name in asked or meaning.FIELDS:
        item = raw_fields.get(name)
        pointers[name] = (
            Pointer(line=int(item["line"]), text=str(item["text"]))
            if isinstance(item, dict) and "line" in item and "text" in item
            else None
        )
    parsed_fields = parsed.get("fields") or {}
    parser_values = {name: (parsed_fields.get(name) or {}).get("value") for name in meaning.FIELDS}
    pointer_values = {
        name: (resolved[name].value if name in resolved else None) for name in meaning.FIELDS
    }
    merged = {
        name: (pointer_values[name] if pointer_values[name] is not None else parser_values[name])
        for name in meaning.FIELDS
    }
    return MeaningAnswer(
        pointers=pointers,
        parser_values=parser_values,
        pointer_values=pointer_values,
        values=merged,
    )


def make_agent(llm) -> object:
    """The agent under test: one model call, then the project's own pointer resolution."""

    async def extract(case: Case, feedback: object = None) -> AgentReply:
        text = str(case.payload)
        parsed = parse_invoice(text, items=parse_items(text))
        request = meaning.build_request(text, meaning.gaps(parsed))
        schema = request["response_format"]["json_schema"]["schema"]
        response = await llm.complete(
            LLMRequest(
                model=request["model"],
                messages=[
                    Message(role="user", content=request["messages"][0]["content"])
                ],
                temperature=float(request["temperature"]),
                max_tokens=int(request["max_tokens"]),
                response_schema=schema,
            )
        )
        plan = meaning.parse_plan(response.text)
        asked = meaning.gaps(parsed)
        return AgentReply(
            attempt=_answer_for(plan, text, parsed, asked),
            usages=(usage_of(response, PRICING),),
        )

    return extract


async def parser_agent(case: Case, feedback: object = None) -> MeaningAnswer:
    """The deterministic reader alone: the number the meaning layer has to beat."""
    text = str(case.payload)
    parsed = parse_invoice(text, items=parse_items(text))
    fields = parsed.get("fields") or {}
    values = {name: (fields.get(name) or {}).get("value") for name in meaning.FIELDS}
    return MeaningAnswer(pointers={}, parser_values=values, pointer_values={}, values=values)


def build_llm(record: bool, replay: bool):
    if replay:
        if not CASSETTE.exists():
            raise SystemExit(f"no cassette at {CASSETTE}; record one with MEANING_RECORD=1 first")
        return ReplayClient(CASSETTE, mode="replay")
    from qagent.llm import OpenAICompatibleClient

    key = meaning._api_key()
    if not key:
        raise SystemExit("no Groq key; use MEANING_REPLAY=1 for the committed cassette")
    live = OpenAICompatibleClient(
        model=MODEL,
        base_url=BASE_URL,
        api_key=key,
        structured_output="json_schema",
        timeout_s=120.0,
    )
    if record:
        # `auto` fills the gaps and replays what is already recorded: a run that hit a
        # provider hiccup is repaired without paying for the answers it already has.
        mode = "auto" if CASSETTE.exists() else "record"
        return ReplayClient(CASSETTE, inner=live, mode=mode)
    return live


def category(name: str) -> str:
    lowered = name.lower()
    if any(mark in lowered for mark in ("ocr", "noise", "fallback", "prefix", "spaced", "parties")):
        return "edge_case"
    return "happy_path"


def dataset() -> list[EvalCase]:
    raw = json.loads(CASES.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for case in raw:
        expected = {"values": dict(case["expected"])}
        cases.append(
            EvalCase(
                case_id=case["name"],
                category=category(case["name"]),  # type: ignore[arg-type]
                payload=case["text"],
                expected=expected,
            )
        )
    return cases


async def run(args: argparse.Namespace) -> None:
    from qagent import guard

    cases = dataset()
    config = QAConfig(
        judge=JudgePolicy(enabled=False),  # the golden values judge; no second model needed
        retry=RetryPolicy(max_attempts=1),
    )
    if args.parser:
        guarded = guard(parser_agent, PRESET, config=config)
        label = "parser"
    else:
        guarded = guard(make_agent(build_llm(args.record, args.replay)), PRESET, config=config)
        label = "meaning"

    result = await evaluate(
        guarded,
        cases,
        concurrency=args.concurrency,
        revision=meaning.__name__ + ":" + label,
    )
    stamp = label
    json_path = report.write_json(result, OUT / f"{stamp}.json")
    report.write_markdown(result, OUT / f"{stamp}.md")
    metrics = result.metrics
    print(
        f"{label}: {metrics.cases} cases, pass {metrics.pass_rate_final:.1%}, "
        f"false pass {metrics.false_pass_rate:.1%} -> {json_path}"
    )
    if not args.parser and not args.no_diff:
        baseline_path = OUT / "parser.json"
        if baseline_path.exists():
            diff = compare(baseline_path, json_path)
            diff_path = write_diff(diff, OUT / "parser_vs_meaning.md")
            print(f"diff: {len(diff.changed_cases)} changed case(s) -> {diff_path}")
        else:
            print("diff: run with --parser first to create .run/meaning/parser.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser", action="store_true", help="score the parser alone, no model")
    parser.add_argument("--record", action="store_true", default=bool(os.environ.get("MEANING_RECORD")))
    parser.add_argument("--replay", action="store_true", default=bool(os.environ.get("MEANING_REPLAY")))
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--no-diff", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
