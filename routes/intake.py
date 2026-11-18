"""The two other doors into the pipeline: the watched folder and the mailbox."""
import asyncio

from fastapi import APIRouter

import intake
import mailbox
import settings

router = APIRouter()


@router.get('/api/intake')
def intake_state():
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
def mailbox_state():
    return mailbox.status()


@router.post('/api/mailbox/check')
async def mailbox_check():
    result = await asyncio.to_thread(intake._mailbox_poll_once)
    return {**mailbox.status(), 'last_poll': result}
