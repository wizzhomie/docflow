"""Run every scripted check over the synthetic corpus and report what failed.

Run: python3 tools/run_checks.py --pass 0 [--corpus data/synthetic-corpus]

This is the cheap, reproducible layer of the review: it runs after every pass,
before any agent looks at anything. A failure here is not a verdict — it is a
claim for the reviewer to arbitrate: the defect is real, the check is written
wrong, or the check is too narrow for the rule it names. The report keeps that
distinction visible by listing check id, invariant, severity and the exact
documents that failed, so the reviewer can answer per check instead of per
paragraph.

Output: `memory/agent-runs/pass-N/checks.md` and `checks.json` (machine-readable,
so a later pass can compare which checks are new and which stopped failing).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'tools'))

import checks  # noqa: E402

import intake  # noqa: E402

DEFAULT_CORPUS = BASE / 'data' / 'synthetic-corpus'


def run(corpus: Path) -> dict:
    manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
    results = {entry.id: {'passed': 0, 'failed': []} for entry in checks.CHECKS}
    for item in manifest:
        path = corpus / item['file']
        document = intake._process(item['file'], item['file'], path, persist=False)
        for entry in checks.CHECKS:
            held = entry.check(item, document)
            if held:
                results[entry.id]['passed'] += 1
            else:
                results[entry.id]['failed'].append(item['file'])
    return {'documents': len(manifest), 'results': results}


def markdown(report: dict, pass_number: str, corpus: Path) -> str:
    stamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')
    total = report['documents']
    lines = [
        f'# Скриптованные проверки: прогон {pass_number}',
        '',
        f'Корпус: `{corpus.relative_to(BASE)}`, документов: {total}. Прогон: {stamp}.',
        '',
        'Проверки запускаются до ревизора. Каждое падение — не приговор, а вопрос',
        'ревизору: настоящий дефект, неверно написанная проверка или проверка уже',
        'правила. Поэтому ниже у каждой проверки есть инвариант и список файлов.',
        '',
        '| ID | Проверка | Серьёзность | Прошло | Упало |',
        '| --- | --- | --- | --- | --- |',
    ]
    for entry in checks.CHECKS:
        bucket = report['results'][entry.id]
        lines.append(
            f"| {entry.id} | {entry.title} | {entry.severity} | "
            f"{bucket['passed']}/{total} | {len(bucket['failed'])} |"
        )
    lines += ['', '## Детали падений', '']
    for entry in checks.CHECKS:
        failed = report['results'][entry.id]['failed']
        if not failed:
            continue
        lines += [
            f'### {entry.id} — {entry.title}',
            '',
            f'Инвариант: {entry.invariant}',
            '',
            f'Упало ({len(failed)}): ' + ', '.join(f'`{name}`' for name in failed[:25]),
            '',
        ]
        if len(failed) > 25:
            lines += [f'…и ещё {len(failed) - 25}.', '']
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pass', dest='pass_number', default='0')
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args()
    if not (args.corpus / 'manifest.json').exists():
        print(f'no corpus at {args.corpus}; run tools/make_synthetic_corpus.py first')
        return 1
    out = args.out or BASE / 'memory' / 'agent-runs' / f'pass-{args.pass_number}'
    out.mkdir(parents=True, exist_ok=True)
    report = run(args.corpus)
    (out / 'checks.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (out / 'checks.md').write_text(
        markdown(report, args.pass_number, args.corpus), encoding='utf-8'
    )
    failed = {
        entry.id: len(bucket['failed'])
        for entry, bucket in ((checks.by_id(check_id), report['results'][check_id])
                              for check_id in report['results'])
        if bucket['failed']
    }
    print(f"{report['documents']} documents; failing checks: {failed or 'none'}")
    print(f'report: {out / "checks.md"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
