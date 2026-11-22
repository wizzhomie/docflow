"""The fine-tuning tools are run by hand; the suite keeps them from drifting.

The chain in `finetune/` has one failure mode that no model run exposes: the training
record stops matching what the runtime asks. The prompt lives in `meaning.PROMPT` and the
generator imports it instead of copying it, but the corpus, the line numbering and the
pointer labels can still come apart quietly — and a model trained on drifted labels
points at the wrong line with full confidence.

These tests generate a small corpus, build the training set from it, and check what the
training actually depends on: the prompt is the runtime's own, every pointer quotes the
line it names, every answer carries every field, and no document sits in two splits.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import meaning

PYTHON = sys.executable
ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')
SPLITS = ('train', 'val', 'test')


def run(script: str, *args: str) -> None:
    done = subprocess.run([PYTHON, str(ROOT / script), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=180)
    assert done.returncode == 0, f'{script} failed:\n{done.stderr[-2000:]}'


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def numbered(text: str) -> str:
    return '\n'.join(f'{index}\t{line}' for index, line in enumerate(text.splitlines(), 1))


@pytest.fixture(scope='module')
def corpus(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp('corpus')
    run('finetune/corpus/make_text_corpus.py', '--count', '12', '--out', str(out))
    return out


@pytest.fixture(scope='module')
def training(tmp_path_factory, corpus) -> Path:
    out = tmp_path_factory.mktemp('training')
    run('finetune/corpus/make_training_data.py', '--corpus', str(corpus), '--out', str(out))
    return out


def test_a_generated_document_looks_like_a_document(corpus):
    """A corpus that lost its encoding still trains: it just trains on nonsense."""
    text = sorted(corpus.glob('*.txt'))[0].read_text(encoding='utf-8')
    assert 'ТОВ' in text or 'ФОП' in text
    assert 'ПДВ' in text


def test_the_prompt_is_the_one_the_runtime_sends(corpus, training):
    prompts = set()
    for name in SPLITS:
        prompts.update(record['messages'][0]['content']
                       for record in records(training / f'{name}.jsonl'))
    for path in sorted(corpus.glob('*.txt')):
        text = path.read_text(encoding='utf-8')
        expected = meaning.PROMPT.format(fields=', '.join(FIELDS), lines=numbered(text))
        assert expected in prompts, f'{path.name} is in no training split'


def test_every_answer_carries_every_field(training):
    for record in records(training / 'train.jsonl'):
        answer = json.loads(record['messages'][1]['content'])
        assert list(answer) == ['fields']
        assert set(answer['fields']) == set(FIELDS)


def test_every_pointer_quotes_the_line_it_names(corpus, training):
    """The label is the whole supervision: a pointer that quotes another line teaches
    the model to point at the wrong place, and the loss says the model is learning."""
    entries = json.loads((training / 'test_manifest.json').read_text(encoding='utf-8'))
    checked = 0
    for entry in entries:
        lines = (corpus / entry['file']).read_text(encoding='utf-8').splitlines()
        for name, pointer in entry['pointers'].items():
            if not pointer:
                continue
            line = lines[int(pointer['line']) - 1]
            assert pointer['text'] in line, f'{entry["file"]} {name}: {pointer!r}'
            checked += 1
    assert checked >= len(entries) * 3, f'only {checked} pointers to check'


def test_no_document_is_in_two_splits(corpus, training):
    split = json.loads((training / 'split.json').read_text(encoding='utf-8'))
    every = [name for part in SPLITS for name in split[part]]
    assert len(every) == len(set(every)) == len(list(corpus.glob('*.txt')))
    assert all(split[part] for part in SPLITS)
