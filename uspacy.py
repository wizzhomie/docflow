import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from profiles import legal_forms, number_chars

BASE_URL = os.environ.get('USPACY_BASE_URL', '').rstrip('/')
EMAIL = os.environ.get('USPACY_EMAIL', '')
PASSWORD = os.environ.get('USPACY_PASSWORD', '')
STATIC_TOKEN = os.environ.get('USPACY_TOKEN', '')

_session = requests.Session()
_token = STATIC_TOKEN or None
_refresh = None
_expires_at = 0.0

# Retry queue
FAILED_DEALS_PATH = Path(__file__).parent / 'data' / 'failed_deals.jsonl'
FAILED_DEALS_PATH.parent.mkdir(parents=True, exist_ok=True)
_retry_thread: threading.Thread | None = None
_retry_stop = threading.Event()
_retry_lock = threading.Lock()


def configured() -> bool:
    return bool(BASE_URL) and bool(STATIC_TOKEN or (EMAIL and PASSWORD))


def _store(data: dict) -> None:
    global _token, _refresh, _expires_at
    _token = data.get('jwt') or data.get('token') or _token
    if data.get('refreshToken'):
        _refresh = data['refreshToken']
    _expires_at = time.time() + int(data.get('expireInSeconds', 3600)) - 60
    if not _token:
        raise RuntimeError('Uspacy sign-in returned no token')


def _sign_in() -> None:
    if not (EMAIL and PASSWORD):
        raise RuntimeError('Uspacy credentials are not configured')
    res = _session.post(
        f'{BASE_URL}/auth/v1/auth/sign_in',
        json={'email': EMAIL, 'password': PASSWORD},
        timeout=20,
    )
    res.raise_for_status()
    _store(res.json())


def _refresh_access() -> bool:
    if not _refresh:
        return False
    res = _session.post(
        f'{BASE_URL}/auth/v1/auth/refresh_token',
        headers={'Authorization': f'Bearer {_refresh}'},
        timeout=20,
    )
    if res.status_code != 200:
        return False
    _store(res.json())
    return True


def _ensure_token() -> None:
    if _token and time.time() < _expires_at:
        return
    if _refresh_access():
        return
    if EMAIL and PASSWORD:
        _sign_in()


def _get(path: str, params: dict | None = None) -> requests.Response:
    _ensure_token()
    return _session.get(
        f'{BASE_URL}{path}',
        headers={'Authorization': f'Bearer {_token}'},
        params=params,
        timeout=20,
    )


def _post(path: str, payload: dict) -> requests.Response:
    _ensure_token()
    return _session.post(
        f'{BASE_URL}{path}',
        json=payload,
        headers={'Authorization': f'Bearer {_token}'},
        timeout=25,
    )


MAX_PAGES = 50

_LEGAL_FORMS = legal_forms()
_NUMBER_CHARS = number_chars()


def _page_through(path: str) -> list[dict]:
    """Read a whole CRM collection.

    Uspacy ignores search/filter/limit on these endpoints and returns the first
    page regardless, so every lookup is matched client-side; only `page` works.
    """
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        res = _get(path, params={'page': page})
        if res.status_code != 200:
            break
        payload = res.json()
        data = payload.get('data') or []
        if not data:
            break
        items.extend(data)
        meta = (payload.get('meta') or {}).get('meta') or {}
        last_page = meta.get('last_page')
        if last_page and page >= last_page:
            break
    return items


def _norm_name(value: str) -> str:
    text = (value or '').casefold()
    for ch in '«»"\'\u2018\u2019\u201c\u201d(),.;:':
        text = text.replace(ch, ' ')
    return ' '.join(text.split())


def _name_forms(value: str) -> set[str]:
    """Comparable name forms: as written and without the legal form."""
    norm = _norm_name(value)
    if not norm:
        return set()
    forms = {norm}
    head, _, tail = norm.partition(' ')
    if head in _LEGAL_FORMS and tail:
        forms.add(tail)
    return forms


def _digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def find_company_by_tax_id(tax_id: str) -> dict | None:
    """Find a company whose stored tax ID matches; None instead of a wrong hit."""
    needle = _digits(tax_id)
    if len(needle) < 8:
        return None
    for company in _page_through('/crm/v1/entities/companies'):
        haystack = ' '.join(
            str(company.get(key) or '')
            for key in ('comments', 'description', 'company_name')
        )
        if needle in _digits(haystack):
            return company
    return None


def find_company_by_name(name: str) -> dict | None:
    """Find a company by name; None rather than an arbitrary company."""
    wanted = _name_forms(name)
    if not wanted:
        return None
    for company in _page_through('/crm/v1/entities/companies'):
        for field in ('title', 'company_name'):
            if _name_forms(company.get(field) or '') & wanted:
                return company
    return None


def _company_fields(details: dict) -> dict:
    """Uspacy expects phone and email as arrays, address and site as strings."""
    payload = {}
    if details.get('phone'):
        payload['phone'] = [{'value': details['phone']}]
    if details.get('email'):
        payload['email'] = [{'value': details['email']}]
    if details.get('address'):
        payload['address'] = details['address']
    if details.get('site'):
        payload['site'] = details['site']
    return payload


def _comment_block(details: dict) -> str:
    lines = [line for line in details.get('comment_lines') or [] if line]
    return '\n'.join(lines)


def create_company(name: str, details: dict | None = None) -> dict:
    """Create a company from the document requisites."""
    details = details or {}
    payload = {'title': name, 'company_name': name, **_company_fields(details)}
    comments = _comment_block(details)
    if comments:
        payload['comments'] = comments
    res = _post('/crm/v1/entities/companies', payload)
    res.raise_for_status()
    return res.json()


def enrich_company(company: dict, details: dict | None = None) -> dict:
    """Fill company fields that are still empty. A human-entered value is never overwritten."""
    details = details or {}
    payload = {}
    for key, value in _company_fields(details).items():
        if value and not (company.get(key) or ''):
            payload[key] = value
    comments = (company.get('comments') or '').strip()
    lines = [line for line in details.get('comment_lines') or [] if line and line not in comments]
    if lines:
        payload['comments'] = (comments + '\n' if comments else '') + '\n'.join(lines)
    if not payload:
        return company
    res = _patch(f'/crm/v1/entities/companies/{company.get("id")}', payload)
    if res.status_code != 200:
        return company
    return res.json()


def find_or_create_company(name: str, details: dict | None = None) -> dict:
    """Find a company by tax ID or name and enrich it; create it when it is new."""
    details = details or {}
    tax_id = details.get('tax_id')
    company = find_company_by_tax_id(tax_id) if tax_id else None
    if company is None:
        company = find_company_by_name(name)
    if company is not None:
        return enrich_company(company, details)
    return create_company(name, details)


def find_contact_by_name(name: str, company_id: int | None = None) -> dict | None:
    """Find a contact by name, optionally restricted to one company."""
    wanted = _name_forms(name)
    if not wanted:
        return None
    for contact in _page_through('/crm/v1/entities/contacts'):
        if company_id:
            linked = [c.get('id') for c in contact.get('companies') or []]
            if company_id not in linked:
                continue
        if _name_forms(contact.get('title') or '') & wanted:
            return contact
    return None


def create_contact(
    name: str,
    company_id: int | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict:
    """Create a contact with the full name as title and no guessed name split."""
    payload = {'title': name}
    if company_id:
        payload['companies'] = [company_id]
    fields = _company_fields({'phone': phone, 'email': email})
    payload.update(fields)
    res = _post('/crm/v1/entities/contacts', payload)
    res.raise_for_status()
    return res.json()


def find_or_create_contact(
    name: str,
    company_id: int | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict | None:
    if not name:
        return None
    contact = find_contact_by_name(name, company_id)
    if contact is not None:
        return contact
    return create_contact(name, company_id, phone, email)


def _has_number(title: str, number: str) -> bool:
    """Whole-token match: invoice "55" must not match the title of "2026-455"."""
    pattern = (
        rf'(?<![{_NUMBER_CHARS}\-])'
        rf'{re.escape(number.casefold())}'
        rf'(?![{_NUMBER_CHARS}\-])'
    )
    return re.search(pattern, (title or '').casefold()) is not None


def _patch(path: str, payload: dict) -> requests.Response:
    _ensure_token()
    res = _session.patch(
        f'{BASE_URL}{path}',
        json=payload,
        headers={'Authorization': f'Bearer {_token}'},
        timeout=20,
    )
    if res.status_code == 401 and EMAIL and PASSWORD:
        _sign_in()
        res = _session.patch(
            f'{BASE_URL}{path}',
            json=payload,
            headers={'Authorization': f'Bearer {_token}'},
            timeout=20,
        )
    return res


def find_deal_by_invoice(number: str, supplier_tax_id: str | None = None) -> dict | None:
    """Find a deal for this invoice number, and supplier when the tax ID is known."""
    if not number:
        return None
    needle = _digits(supplier_tax_id)
    for deal in _page_through('/crm/v1/entities/deals'):
        if not _has_number(deal.get('title', ''), number):
            continue
        if not needle:
            return deal
        if needle in _digits(deal.get('comments', '')):
            return deal
    return None


@dataclass
class FailedDeal:
    payload: dict
    attempts: int = 0
    last_error: str = ''
    created_at: float = 0.0


def retry_deal_payload(payload: dict) -> dict:
    """Send a deal payload once, with the same token refresh a first attempt gets.

    One place knows how to re-send a deal: the confirm, the retry worker and the
    database-backed job all call it, so a retry cannot drift from the original
    request.
    """
    res = _post('/crm/v1/entities/deals', payload)
    if res.status_code == 401 and EMAIL and PASSWORD:
        _sign_in()
        res = _post('/crm/v1/entities/deals', payload)
    res.raise_for_status()
    return res.json()


def _enqueue_retry(deal: FailedDeal) -> bool:
    """Put the failed deal in the database queue, if there is a database.

    Lazy import: the file-based demo runs on a machine without PostgreSQL, and a
    module that cannot be imported must not stop the process from starting.
    """
    try:
        import jobqueue
    except ImportError:
        return False
    try:
        jobqueue.enqueue(
            company_id=int(os.environ.get('DOCFLOW_DEFAULT_COMPANY', '1')),
            kind='crm.deal',
            payload={'deal': deal.payload},
            delay_seconds=30,
        )
        return True
    except Exception:  # noqa: BLE001 - no database, no queue: fall back to the file
        return False


def _save_failed_deal(deal: FailedDeal) -> None:
    """Keep a failed deal where a worker can find it.

    The database queue when there is one — durable across a restart, drainable by a
    second process, with the attempt count kept — and the JSONL file when there is
    not, because the demo must keep working without PostgreSQL.
    """
    if _enqueue_retry(deal):
        return
    with _retry_lock, FAILED_DEALS_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(asdict(deal), ensure_ascii=False) + '\n')


def _load_failed_deals() -> list[FailedDeal]:
    if not FAILED_DEALS_PATH.exists():
        return []
    deals = []
    with FAILED_DEALS_PATH.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            deals.append(FailedDeal(**data))
    return deals


def _clear_failed_deals() -> None:
    with _retry_lock:
        FAILED_DEALS_PATH.write_text('')


def _retry_worker() -> None:
    """Background worker that retries failed deals with exponential backoff."""
    base_delay = 30  # seconds
    max_delay = 3600  # 1 hour
    while not _retry_stop.is_set():
        time.sleep(10)
        if _retry_stop.is_set():
            break
        deals = _load_failed_deals()
        if not deals:
            continue
        remaining = []
        for deal in deals:
            if _retry_stop.is_set():
                remaining.append(deal)
                break
            delay = min(base_delay * (2**deal.attempts), max_delay)
            if time.time() - deal.created_at < delay:
                remaining.append(deal)
                continue
            try:
                _ensure_token()
                res = _session.post(
                    f'{BASE_URL}/crm/v1/entities/deals',
                    json=deal.payload,
                    headers={'Authorization': f'Bearer {_token}'},
                    timeout=25,
                )
                if res.status_code == 401 and EMAIL and PASSWORD:
                    _sign_in()
                    res = _session.post(
                        f'{BASE_URL}/crm/v1/entities/deals',
                        json=deal.payload,
                        headers={'Authorization': f'Bearer {_token}'},
                        timeout=25,
                    )
                if res.status_code >= 200 and res.status_code < 300:
                    # Success - don't re-queue
                    continue
                else:
                    deal.attempts += 1
                    deal.last_error = f'HTTP {res.status_code}: {res.text[:200]}'
                    remaining.append(deal)
            except Exception as exc:
                deal.attempts += 1
                deal.last_error = str(exc)
                remaining.append(deal)
        # Rewrite file with remaining
        with _retry_lock, FAILED_DEALS_PATH.open('w', encoding='utf-8') as f:
            for d in remaining:
                f.write(json.dumps(asdict(d), ensure_ascii=False) + '\n')


def start_retry_worker() -> None:
    """Start background retry worker (call once at app startup)."""
    global _retry_thread
    if _retry_thread and _retry_thread.is_alive():
        return
    _retry_stop.clear()
    _retry_thread = threading.Thread(target=_retry_worker, daemon=True)
    _retry_thread.start()


def stop_retry_worker() -> None:
    """Stop background retry worker."""
    _retry_stop.set()
    if _retry_thread:
        _retry_thread.join(timeout=5)


def create_deal(
    *,
    title: str,
    amount,
    currency: str = 'UAH',
    comments: str = '',
    company_id: int | None = None,
    contact_ids: list[int] | None = None,
) -> dict:
    if not configured():
        raise RuntimeError('Uspacy is not configured')
    payload = {
        'title': title,
        'amount_of_the_deal': {
            'currency': currency or 'UAH',
            'value': round(float(amount), 2) if amount not in (None, '') else 0,
        },
        'comments': comments,
    }
    if company_id:
        payload['companies'] = [company_id]
    if contact_ids:
        payload['contacts'] = list(contact_ids)
    try:
        return retry_deal_payload(payload)
    except Exception as exc:
        # Queue for retry
        failed = FailedDeal(
            payload=payload,
            attempts=0,
            last_error=str(exc),
            created_at=time.time(),
        )
        _save_failed_deal(failed)
        raise
