"""Issue, list and revoke keys for machine clients of the API.

Run:

    python3 tools/api_key.py issue --label "Platform ERP" --scopes documents:write,documents:read
    python3 tools/api_key.py list
    python3 tools/api_key.py revoke 3f2a91c4d0b7

`issue` prints the token once. The file keeps only its digest, so the token cannot
be recovered from the file: if it is lost, revoke the key and issue another one.
A key with `--company` writes through the PostgreSQL tenant of that company; a key
without it works on the file store.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import apikeys  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='API keys for DocFlow integrations')
    commands = parser.add_subparsers(dest='command', required=True)

    issue = commands.add_parser('issue', help='create a key and print its token once')
    issue.add_argument('--label', required=True, help='which integration this key is for')
    issue.add_argument(
        '--scopes',
        default=','.join(apikeys.SCOPES),
        help=f'comma-separated, from: {", ".join(apikeys.SCOPES)}',
    )
    issue.add_argument(
        '--company',
        type=int,
        default=None,
        help='company id for the database tenant; omit for the file store',
    )

    commands.add_parser('list', help='keys without their secrets')

    revoke = commands.add_parser('revoke', help='stop a key from verifying')
    revoke.add_argument('key_id')

    args = parser.parse_args(argv)

    if args.command == 'issue':
        try:
            record, token = apikeys.issue(
                args.label, [scope for scope in args.scopes.split(',') if scope.strip()], args.company
            )
        except ValueError as error:
            print(f'refused: {error}', file=sys.stderr)
            return 2
        print(f'id:      {record["id"]}')
        print(f'label:   {record["label"]}')
        print(f'scopes:  {", ".join(record["scopes"])}')
        print(f'company: {record["company_id"] if record["company_id"] else "file store"}')
        print()
        print(token)
        print()
        print('The token is not stored: copy it now.', file=sys.stderr)
        return 0

    if args.command == 'list':
        keys = apikeys.listing()
        if not keys:
            print('no keys issued')
            return 0
        for record in keys:
            state = 'revoked' if record['revoked'] else 'active'
            print(
                f'{record["id"]}  {state:7}  {",".join(record["scopes"]):50}  '
                f'{record["label"]}  (used: {record["last_used_at"] or "never"})'
            )
        return 0

    if not apikeys.revoke(args.key_id):
        print(f'no active key with id {args.key_id}', file=sys.stderr)
        return 1
    print(f'revoked {args.key_id}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
