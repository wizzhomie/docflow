#!/usr/bin/env python3
"""Evaluate the agent and the rules baseline on the same twelve scripted tasks.

Run from the project root while DocFlow (:8765) and llama-server (:8080) are up:

    .venv/bin/python eval/run_eval.py               # agent + rules baseline
    .venv/bin/python eval/run_eval.py --baseline    # rules only (fast, no model)
    .venv/bin/python eval/run_eval.py --only queue-overview

The result is a table plus eval/results.json so numbers in the README can be
re-checked instead of trusted.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docflow_operator.agent import Agent, ModelClient  # noqa: E402
from docflow_operator.config import get_settings  # noqa: E402
from docflow_operator.docflow import DocFlowClient  # noqa: E402
from docflow_operator.fallback import run as run_rules  # noqa: E402
from docflow_operator.tools import Toolbox  # noqa: E402
from eval.scoring import score  # noqa: E402

SCENARIOS = Path(__file__).resolve().parent / 'scenarios.json'
RESULTS = Path(__file__).resolve().parent / 'results.json'


def run_agent(scenario: dict, toolbox: Toolbox, settings) -> dict:
    model = ModelClient(settings.model_url, settings.model_name, settings.model_timeout,
                        settings.temperature)
    started = time.time()
    try:
        result = Agent(model, toolbox, settings.max_steps, settings.time_budget).run(scenario['task'])
    finally:
        model.close()
    return {
        'steps': [{'tool': step['tool'], 'arguments': step['arguments'], 'ok': step['ok'],
                   'refused': step.get('refused', False)} for step in result['steps']],
        'answer': result['answer'],
        'model': result.get('model'),
        'fallback': result['fallback'],
        'failure': result['failure'],
        'elapsed_ms': round((time.time() - started) * 1000),
    }


def run_baseline(scenario: dict, toolbox: Toolbox) -> dict:
    started = time.time()
    steps, answer = run_rules(scenario['task'], toolbox)
    return {
        'steps': [{'tool': step['tool'], 'arguments': step['arguments'], 'ok': step['ok']}
                  for step in steps],
        'answer': answer,
        'fallback': False,
        'failure': None,
        'elapsed_ms': round((time.time() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true', help='rules baseline only, no model')
    parser.add_argument('--only', help='run one scenario id')
    parser.add_argument('--json', default=str(RESULTS))
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    data = json.loads(SCENARIOS.read_text(encoding='utf-8'))
    scenarios = data['scenarios']
    if args.only:
        scenarios = [scenario for scenario in scenarios if scenario['id'] == args.only]
        if not scenarios:
            print(f'unknown scenario {args.only!r}', file=sys.stderr)
            return 2

    docflow = DocFlowClient(settings.docflow_url, settings.docflow_timeout)
    toolbox = Toolbox(docflow, settings.drafts_dir)
    rows = []
    try:
        for scenario in scenarios:
            entry = {'id': scenario['id'], 'task': scenario['task']}
            if not args.baseline:
                agent = run_agent(scenario, toolbox, settings)
                entry['agent'] = {**agent, **score(scenario, agent['steps'], agent['answer'])}
            baseline = run_baseline(scenario, toolbox)
            entry['baseline'] = {**baseline, **score(scenario, baseline['steps'], baseline['answer'])}
            rows.append(entry)
            agent_part = ''
            if 'agent' in entry:
                agent_part = f"agent={'ok' if entry['agent']['success'] else 'fail'} " \
                             f"{entry['agent']['elapsed_ms']}ms | "
            print(f"{scenario['id']:18s} {agent_part}baseline="
                  f"{'ok' if entry['baseline']['success'] else 'fail'} {entry['baseline']['elapsed_ms']}ms")
    finally:
        docflow.close()

    def summary(key: str) -> dict:
        selected = [row[key] for row in rows if key in row]
        if not selected:
            return {}
        return {
            'success': sum(1 for item in selected if item['success']),
            'valid_calls': sum(1 for item in selected if item['valid_calls']),
            'total': len(selected),
            'avg_ms': round(sum(item['elapsed_ms'] for item in selected) / len(selected)),
            'fallbacks': sum(1 for item in selected if item.get('fallback')),
        }

    report = {'rows': rows, 'summary': {'agent': summary('agent'), 'baseline': summary('baseline')}}
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for key in ('agent', 'baseline'):
        item = report['summary'][key]
        if item:
            print(f"\n{key}: {item['success']}/{item['total']} success, "
                  f"{item['valid_calls']}/{item['total']} valid calls, "
                  f"avg {item['avg_ms']} ms, fallbacks {item['fallbacks']}")
    print(f"\nresults written to {args.json}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
