"""Structured logs and error reporting for the demo service.

One line per request, as JSON, because a line that a person reads and a line that
a machine filters are different things and this file serves both: the operator
runs `tail -f`, an alert runs `jq`. Each line carries a request id, so a failed
request can be followed through the log even when the person cannot reproduce it.

Errors go to the same file with their traceback. If `SENTRY_DSN` is set (GlitchTip
speaks the same protocol), the error is also sent there, with the request id, the
company and the person attached as tags — which is the whole point of the exercise:
an error without a tenant is a ticket called "something broke for someone".
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

try:  # error reporting is optional: the demo runs without the SDK installed
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    SENTRY_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    sentry_sdk = None
    SENTRY_AVAILABLE = False

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("DOCFLOW_LOG_DIR", ROOT / "data" / "logs"))
LOG_FILE = LOG_DIR / "app.jsonl"
COOKIE = "docflow_session"

logger = logging.getLogger("docflow")
access_logger = logging.getLogger("docflow.access")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the fields an operator actually greps."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms", "user", "company"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())

    for name in ("docflow", "docflow.access", "uvicorn.error"):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.setLevel(logging.INFO)
        target.propagate = False


def init_sentry() -> bool:
    """Turn error reporting on only when a DSN is configured."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn or not SENTRY_AVAILABLE:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("DOCFLOW_ENV", "demo"),
        release=os.environ.get("DOCFLOW_RELEASE", "docflow@demo"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_RATE", "0.1")),
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    return True


def _person(request: Request) -> tuple[str | None, str | None]:
    """Who made the request, for the log line.

    The session cookie is a bearer token, so it never reaches the log: the line
    carries a short digest of it instead, which is enough to follow one person's
    requests through the file and useless to anyone who reads it. Routes that do
    resolve the person record the e-mail on the request, and the line prefers
    that — a log that says "someone" answers nothing.
    """
    user = getattr(request.state, "user", None)
    if user:
        return (user, getattr(request.state, "company", None))
    token = request.cookies.get(COOKIE)
    if token:
        return ("session:" + hashlib.sha256(token.encode()).hexdigest()[:8], None)
    return (None, None)


def install(app: FastAPI) -> dict[str, bool]:
    """Wire logging, the request log and error reporting into the application."""
    setup_logging()
    reporting = init_sentry()

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        user, _ = _person(request)
        if SENTRY_AVAILABLE:
            sentry_sdk.set_tag("request_id", request_id)
            if user:
                sentry_sdk.set_user({"id": user})
        try:
            response = await call_next(request)
        except Exception:
            duration = round((time.perf_counter() - started) * 1000, 1)
            user, company = _person(request)
            access_logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": duration,
                    "user": user,
                    "company": company,
                },
            )
            raise
        duration = round((time.perf_counter() - started) * 1000, 1)
        # Read the person again: the route may have resolved it while handling
        # the request, and the line should say who, not which cookie was sent.
        user, company = _person(request)
        access_logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
                "user": user,
                "company": company,
            },
        )
        response.headers["X-Request-Id"] = request_id
        return response

    logger.info("observability ready", extra={"company": None})
    return {"logging": True, "sentry": reporting}


def status() -> dict[str, Any]:
    """What the service thinks it has: used by /api/health/detail."""
    return {
        "log_file": str(LOG_FILE),
        "log_exists": LOG_FILE.exists(),
        "sentry": bool((os.environ.get("SENTRY_DSN") or "").strip()) and SENTRY_AVAILABLE,
        "sentry_sdk": SENTRY_AVAILABLE,
        "sentry_environment": os.environ.get("DOCFLOW_ENV", "demo"),
    }
