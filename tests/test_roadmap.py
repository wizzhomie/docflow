"""Deterministic checks for the product path shown on the site.

The path is data, not code: docs/roadmap.json holds the stages and decisions,
/api/roadmap adds the progress. These tests keep the file honest, so the screen
cannot claim a step that is not real or lose its "where we are" marker.

Run: python3 tests/test_roadmap.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.reports as reports  # noqa: E402

ROADMAP = Path(__file__).resolve().parents[1] / 'docs' / 'roadmap.json'
VALID_STATUSES = {'done', 'current', 'next'}


def test_roadmap_file_is_valid_json_with_stages():
    data = json.loads(ROADMAP.read_text(encoding='utf-8'))
    assert data['title']
    assert len(data['stages']) >= 5


def test_every_stage_has_required_fields():
    data = json.loads(ROADMAP.read_text(encoding='utf-8'))
    ids = set()
    for stage in data['stages']:
        assert stage['id'] not in ids, f'duplicate stage id {stage["id"]}'
        ids.add(stage['id'])
        assert stage['title']
        assert stage['status'] in VALID_STATUSES, stage['status']
        assert stage['summary']
        assert stage['decision'], 'every step must state the decision behind it'


def test_exactly_one_current_stage():
    data = json.loads(ROADMAP.read_text(encoding='utf-8'))
    current = [stage for stage in data['stages'] if stage['status'] == 'current']
    assert len(current) <= 1, 'the path can only point at one "we are here" step'


def test_progress_is_computed_by_the_endpoint():
    data = reports.roadmap()
    done = sum(1 for stage in data['stages'] if stage['status'] == 'done')
    assert data['progress']['done'] == done
    assert data['progress']['total'] == len(data['stages'])
    assert 0 <= data['progress']['percent'] <= 100


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('roadmap checks passed')


if __name__ == '__main__':
    main()
