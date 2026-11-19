"""One error event in GlitchTip for a failed healthcheck.

Run with the DocFlow virtualenv: it is the environment that already has the SDK
the application uses. Without a DSN the check only logs to the file — a demo with
no tracker must still be checked.
"""
import os
import sys

dsn = (os.environ.get('SENTRY_DSN') or '').strip()
if not dsn:
    raise SystemExit(0)

import sentry_sdk  # noqa: E402

sentry_sdk.init(
    dsn=dsn,
    environment=os.environ.get('DOCFLOW_ENV', 'demo'),
    release='docflow@healthcheck',
    traces_sample_rate=0.0,
)
message = ' '.join(sys.argv[1:]).strip() or 'DocFlow healthcheck failed'
sentry_sdk.capture_message(message, level='error')
sentry_sdk.flush(timeout=5)
