"""Report one healthcheck to an external dead-man's switch.

A check that runs on the machine cannot report that the machine is down: the
timer is down with it. The switch (Healthchecks.io and its kin) inverts the
question — it alerts when the pings *stop* — and this is the half that pings.

A healthy run pings the base URL; a failed one pings `/fail`, so the alert
arrives immediately instead of after the grace period. With no
`DOCFLOW_PING_URL` configured nothing happens and the file log stays the record:
a demo without a switch must still be checked.

    python3 ping.py success
    python3 ping.py fail
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request


def endpoint(base: str, ok: bool) -> str:
    """The URL the switch listens on: `/fail` for a problem, the base URL otherwise."""
    return base.rstrip('/') + ('' if ok else '/fail')


def ping(
    url: str,
    *,
    attempts: int = 2,
    timeout: float = 10.0,
    pause: float = 2.0,
    opener=urllib.request.urlopen,
) -> bool:
    """True when the switch answered; two attempts, because one drop is not an outage."""
    for attempt in range(attempts):
        try:
            with opener(url, timeout=timeout) as response:
                response.read()
            return True
        except (urllib.error.URLError, OSError):
            if attempt + 1 == attempts:
                return False
            time.sleep(pause)
    return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ok = args[:1] != ['fail']
    base = (os.environ.get('DOCFLOW_PING_URL') or '').strip()
    if not base:
        return 0
    if ping(endpoint(base, ok)):
        return 0
    print('ping: the dead-man switch did not answer', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
