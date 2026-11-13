"""Supplier-specific profiles registry.

Each supplier can have custom label anchors, field patterns, and validation overrides.
Profiles are loaded by tax_id (ЄДРПОУ/ІПН) or supplier name fallback.
"""

from importlib import import_module
from pathlib import Path
from typing import Any

from profiles.invoice_ua import LABELS as BASE_LABELS
from profiles.invoice_ua import TAX_ID_PREFIXES as BASE_TAX_PREFIXES

SUPPLIERS_DIR = Path(__file__).parent
_cache: dict[str, dict[str, Any]] = {}


def _load_supplier_module(tax_id: str) -> dict | None:
    """Load supplier profile module by tax_id."""
    # Try exact tax_id match
    module_path = SUPPLIERS_DIR / f'{tax_id}.py'
    if module_path.exists():
        module_name = f'profiles.suppliers.{tax_id}'
        try:
            module = import_module(module_name)
            return getattr(module, 'PROFILE', {})
        except Exception:
            return None
    return None


def get_supplier_profile(tax_id: str | None = None, supplier_name: str | None = None) -> dict:
    """Get merged profile: base invoice_ua + supplier overrides."""
    cache_key = tax_id or supplier_name or 'default'
    if cache_key in _cache:
        return _cache[cache_key]

    profile = {
        'labels': BASE_LABELS.copy(),
        'tax_prefixes': BASE_TAX_PREFIXES.copy(),
        'overrides': {},
    }

    if tax_id:
        supplier_profile = _load_supplier_module(tax_id)
        if supplier_profile:
            # Merge labels
            for key, value in supplier_profile.get('labels', {}).items():
                if key in profile['labels']:
                    profile['labels'][key] = value + profile['labels'][key]  # supplier first
                else:
                    profile['labels'][key] = value
            # Merge tax prefixes
            if 'tax_prefixes' in supplier_profile:
                profile['tax_prefixes'] = supplier_profile['tax_prefixes'] + profile['tax_prefixes']
            # Other overrides
            profile['overrides'] = supplier_profile.get('overrides', {})

    _cache[cache_key] = profile
    return profile


def clear_cache() -> None:
    _cache.clear()
