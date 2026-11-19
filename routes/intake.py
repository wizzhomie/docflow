"""The two other doors into the pipeline: the watched folder and the mailbox."""
import asyncio

from fastapi import APIRouter, Request

import intake
import mailbox
import pgapi
import settings

router = APIRouter()


@router.get('/api/intake')
def intake_state(request: Request):
    # The folder contents are the operator's working data: a visitor of the public
    # demo gets the shape of the answer and no file names.
    if pgapi.person(request) is None:
        return {'folder': '', 'waiting': [], 'processed': 0}
    files = (
        sorted(p.name for p in settings.INBOX.iterdir() if p.is_file())
        if settings.INBOX.exists()
        else []
    )
    return {
        'folder': str(settings.INBOX.relative_to(settings.BASE)),
        'waiting': files,
        'processed': len(list(settings.INBOX_DONE.glob('*')))
        if settings.INBOX_DONE.exists()
        else 0,
    }


@router.post('/api/intake/scan')
def intake_scan():
    return {'ingested': intake._ingest_folder_once()}


@router.get('/api/mailbox')
def mailbox_state(request: Request):
    # The mailbox is the operator's channel; its address and counters stay inside.
    if pgapi.person(request) is None:
        return {'configured': False, 'messages': 0, 'attachments': 0}
    return mailbox.status()


@router.post('/api/mailbox/check')
async def mailbox_check():
    result = await asyncio.to_thread(intake._mailbox_poll_once)
    return {**mailbox.status(), 'last_poll': result}
