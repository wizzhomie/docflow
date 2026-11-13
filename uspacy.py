import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

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


def find_company_by_tax_id(tax_id: str) -> dict | None:
    """Find company by tax ID (ЄДРПОУ/ІПН) using search."""
    if not tax_id:
        return None
    res = _get('/crm/v1/entities/companies', params={'search': tax_id, 'limit': 5})
    if res.status_code != 200:
        return None
    for company in res.json().get('data', []):
        if tax_id in (company.get('title', '') + company.get('company_name', '')):
            return company
    return None


def find_company_by_name(name: str) -> dict | None:
    """Find company by exact name match."""
    if not name:
        return None
    res = _get('/crm/v1/entities/companies', params={'filter[title]': name, 'limit': 5})
    if res.status_code != 200:
        return None
    data = res.json().get('data', [])
    return data[0] if data else None


def create_company(name: str, tax_id: str | None = None) -> dict:
    """Create company with name and optional tax_id in comments."""
    payload = {'title': name, 'company_name': name}
    if tax_id:
        payload['comments'] = f'ЄДРПОУ/ІПН: {tax_id}'
    res = _post('/crm/v1/entities/companies', payload)
    if res.status_code == 401 and EMAIL and PASSWORD:
        _sign_in()
        res = _post('/crm/v1/entities/companies', payload)
    res.raise_for_status()
    return res.json()


def find_or_create_company(name: str, tax_id: str | None = None) -> dict:
    """Find existing company by tax_id or name, create if not found."""
    if tax_id:
        company = find_company_by_tax_id(tax_id)
        if company:
            return company
    company = find_company_by_name(name)
    if company:
        return company
    return create_company(name, tax_id)


def find_deal_by_invoice(number: str, supplier_tax_id: str | None = None) -> dict | None:
    """Find existing deal by invoice number and optionally supplier tax_id."""
    if not number:
        return None
    res = _get('/crm/v1/entities/deals', params={'search': number, 'limit': 20})
    if res.status_code != 200:
        return None
    for deal in res.json().get('data', []):
        title = deal.get('title', '')
        if number not in title:
            continue
        if supplier_tax_id:
            comments = deal.get('comments', '')
            if supplier_tax_id in comments:
                return deal
            # Also check companies title (fallback)
            companies = deal.get('companies') or []
            for c in companies:
                if supplier_tax_id in (c.get('title', '') + str(c.get('id', ''))):
                    return deal
        else:
            return deal
    return None


@dataclass
class FailedDeal:
    payload: dict
    attempts: int = 0
    last_error: str = ''
    created_at: float = 0.0


def _save_failed_deal(deal: FailedDeal) -> None:
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
    try:
        res = _post('/crm/v1/entities/deals', payload)
        if res.status_code == 401 and EMAIL and PASSWORD:
            _sign_in()
            res = _post('/crm/v1/entities/deals', payload)
        res.raise_for_status()
        return res.json()
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
