"""The registry of background handlers.

It lives in its own module because `python -m worker` runs the worker as
`__main__`: when `tasks` imported `worker` for the decorator, Python loaded a
second copy of the module, so handlers registered into a registry that the running
loop never read — every job ended as "no handler". A module both sides import by
name has one registry, whichever way the worker was started.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], None]

# What the worker knows how to do; a handler raises when the job failed.
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register the function that runs one kind of job."""

    def register(function: Handler) -> Handler:
        HANDLERS[kind] = function
        return function

    return register
