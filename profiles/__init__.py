"""Document-type registry.

The parser is not told which document it reads: the first lines decide which
profile owns the text, and the profile carries every type-specific anchor. A
new document type is a new profile module registered here, not another branch
inside the extractor.
"""

import re

from profiles import act_ua, invoice_ua

# Order matters: the act markers are more specific than the invoice ones.
PROFILES = [act_ua, invoice_ua]
DEFAULT = invoice_ua

DEFAULT_PROFILE = invoice_ua


def detect_profile(text: str):
    """Pick the profile by the title lines; an unknown document stays an invoice."""
    head = '\n'.join((text or '').strip().splitlines()[:4]).lower()
    for profile in PROFILES:
        for marker in getattr(profile, 'DETECT_MARKERS', []):
            if re.search(marker, head, re.I | re.M):
                return profile
    return DEFAULT
