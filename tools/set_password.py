"""Set or clear the password of a person in the demo database.

Passwords are never stored in the repository and never typed into SQL: this tool
hashes the value and writes only the hash.

    python3 tools/set_password.py owner@demo.local            # prompts twice
    python3 tools/set_password.py owner@demo.local --clear    # Google only
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

from auth import hash_password  # noqa: E402

DSN = os.environ.get("DOCFLOW_PG_DSN", "dbname=docflow_dev")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--clear", action="store_true", help="remove the password (Google only)")
    args = parser.parse_args()

    if args.clear:
        value = None
    else:
        first = getpass.getpass("Password: ")
        if len(first) < 10:
            print("The demo wants at least 10 characters.", file=sys.stderr)
            return 2
        if first != getpass.getpass("Repeat: "):
            print("The two values differ.", file=sys.stderr)
            return 2
        value = hash_password(first)

    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE lower(email) = %s RETURNING id",
            (value, args.email.strip().lower()),
        )
        row = cursor.fetchone()
    if row is None:
        print(f"No person with {args.email}", file=sys.stderr)
        return 1
    print("cleared" if value is None else "password set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
