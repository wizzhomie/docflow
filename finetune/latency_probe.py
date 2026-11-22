"""One meaning-layer request, measured: token counts and wall time on a given server.

The deployment question ("how long does one document take on the server's two cores")
cannot be answered by guessing, so this probe sends the *real* prompt the meaning layer
builds for a *real* document and reads the numbers back from llama.cpp.

    # start the server first, then:
    python latency_probe.py --url http://127.0.0.1:8099/v1/chat/completions --tag qwen3-lora

What it reports, per document: characters and lines of the text, prompt tokens, answer
tokens, prompt-processing speed, generation speed, and the total a reviewer would wait.
The token counts do not depend on the machine; the speeds do. Measuring the same probe
on the model the server already runs (Qwen2.5-1.5B) gives the factor between this
machine and the server, so the Qwen3 number can be translated instead of believed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

FT = Path(__file__).resolve().parent
BASE = FT.parent
DOCFLOW = BASE
sys.path.insert(0, str(DOCFLOW))

# The extractor needs poppler and tesseract on PATH; with them missing OCR silently
# returns an empty string and the measurement would be of an empty document.
for folder in (BASE / '.llm' / 'poppler',):
    for candidate in folder.rglob('bin'):
        os.environ['PATH'] = str(candidate) + os.pathsep + os.environ.get('PATH', '')
os.environ.setdefault('TESSDATA_PREFIX', str(BASE / '.llm' / 'tessdata'))

import meaning  # noqa: E402 - needs the repository root on sys.path
from extractor import extract_text  # noqa: E402


def prompt_for(text: str) -> str:
    """Exactly what meaning._ask builds, so the tokens are the production tokens."""
    lines = text.splitlines()
    shown = lines[:meaning.MAX_LINES]
    numbered = '\n'.join(f'{index}\t{line}' for index, line in enumerate(shown, start=1))
    if len(lines) > len(shown):
        numbered += f'\n…(показано {len(shown)} рядків із {len(lines)})'
    return meaning.PROMPT.format(lines=numbered)


def probe(url: str, model: str, pdf: Path) -> dict:
    text = extract_text(pdf)
    prompt = prompt_for(text)
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0,
        'max_tokens': 700,
    }
    started = time.monotonic()
    response = httpx.post(url, json=payload, timeout=900)
    elapsed = time.monotonic() - started
    response.raise_for_status()
    body = response.json()
    timings = body.get('timings') or {}
    usage = body.get('usage') or {}
    content = body['choices'][0]['message'].get('content') or ''
    prompt_n = timings.get('prompt_n') or usage.get('prompt_tokens') or 0
    answer_n = timings.get('predicted_n') or usage.get('completion_tokens') or 0
    return {
        'file': pdf.name,
        'chars': len(text),
        'lines': len(text.splitlines()),
        'shown_lines': min(len(text.splitlines()), meaning.MAX_LINES),
        'prompt_tokens': prompt_n,
        'answer_tokens': answer_n,
        'prompt_ms': round(timings.get('prompt_ms', 0.0), 1),
        'answer_ms': round(timings.get('predicted_ms', 0.0), 1),
        'prompt_tok_s': round(timings.get('prompt_per_second', 0.0), 2),
        'answer_tok_s': round(timings.get('predicted_per_second', 0.0), 2),
        'wall_s': round(elapsed, 2),
        'answer_head': content.strip()[:160],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8099/v1/chat/completions')
    parser.add_argument('--model', default='local')
    parser.add_argument('--tag', default='probe')
    parser.add_argument('--out', default=str(FT / 'data' / 'latency'))
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()

    rows = []
    for name in args.files:
        row = probe(args.url, args.model, Path(name))
        rows.append(row)
        print(
            f"{row['file']}: {row['lines']} lines / {row['chars']} chars -> "
            f"prompt {row['prompt_tokens']} tok @ {row['prompt_tok_s']} tok/s, "
            f"answer {row['answer_tokens']} tok @ {row['answer_tok_s']} tok/s, "
            f"wall {row['wall_s']} s",
            flush=True,
        )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.tag}.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
