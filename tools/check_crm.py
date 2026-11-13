"""Opt-in health check for the real Uspacy account.

Read-only by default: signs in, reads the first page of companies and deals and
checks that client-side matching finds what it should. With `--write` it also
creates one clearly labelled test deal (and the company, if missing), which is
the only way to prove the write path end to end.

Run:
    set -a; . ./.env; set +a; python3 tools/check_crm.py
    set -a; . ./.env; set +a; python3 tools/check_crm.py --write
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uspacy  # noqa: E402

TEST_TITLE = 'DocFlow connectivity check'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='create one test deal')
    args = parser.parse_args()

    if not uspacy.configured():
        print('not configured: set USPACY_BASE_URL and USPACY_TOKEN or EMAIL/PASSWORD')
        return 1

    try:
        uspacy._ensure_token()
    except Exception as exc:  # noqa: BLE001 - report, do not raise stack traces at operators
        print(f'sign-in failed: {type(exc).__name__}: {exc}')
        return 1
    print(f'auth: ok ({uspacy.BASE_URL})')

    companies = uspacy._page_through('/crm/v1/entities/companies')
    deals = uspacy._page_through('/crm/v1/entities/deals')
    print(f'read: {len(companies)} companies, {len(deals)} deals')

    probe = uspacy.find_company_by_name(TEST_TITLE)
    print(f'matching: probe company -> {probe.get("id") if probe else "not found"}')

    if not args.write:
        print('write: skipped (use --write to create one test deal)')
        return 0

    company = uspacy.find_or_create_company(TEST_TITLE, None)
    deal = uspacy.create_deal(
        title=f'Рахунок №CHECK — {TEST_TITLE}',
        amount=1.0,
        currency='UAH',
        comments='Created by tools/check_crm.py. Safe to delete.',
        company_id=company.get('id'),
    )
    print(f'write: company {company.get("id")}, deal {deal.get("id")}')
    print('delete both in the Uspacy UI after checking the pipeline board')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
