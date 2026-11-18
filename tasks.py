"""What the worker knows how to do.

One module, so the worker does not import the web application and the web
application does not have to know which jobs exist. Each handler is a plain
function that raises when the job failed: the queue turns the exception into a
retry with backoff, or into a dead job when the attempts are spent.
"""

from __future__ import annotations

import os
from typing import Any

import uspacy
from worker import handler


def retry_company() -> int:
    """The company a retried background job is done for.

    The file-based demonstration is one company's workspace, so a deal that failed
    belongs to the demo company. A deployment that runs several companies through
    the database passes the tenant with the job; the row already carries it.
    """
    return int(os.environ.get('DOCFLOW_DEFAULT_COMPANY', '1'))


@handler('crm.deal')
def retry_crm_deal(job: dict[str, Any]) -> None:
    """Re-send a deal the CRM refused, exactly as the confirm tried to.

    The payload is stored, not rebuilt: a retry must send what the person
    confirmed, not what the parser would produce today.
    """
    payload = (job.get('payload') or {}).get('deal') or {}
    if not payload:
        raise ValueError('job has no deal payload')
    uspacy.retry_deal_payload(payload)
