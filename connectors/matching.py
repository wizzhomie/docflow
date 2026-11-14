"""Text matching shared by every CRM connector.

A CRM returns whole pages regardless of filters more often than one would hope,
so entity matching happens on our side and the rules must be identical for each
connector: a tax ID compared by digits, a company name compared without quotes
and legal form, an invoice number compared as a whole token.
"""

import re

from profiles import legal_forms, number_chars

_LEGAL_FORMS = legal_forms()
_NUMBER_CHARS = number_chars()


def digits(value) -> str:
    return re.sub(r'\D', '', str(value or ''))


def norm_name(value) -> str:
    text = str(value or '').casefold()
    for ch in '«»"\'\u2018\u2019\u201c\u201d(),.;:':
        text = text.replace(ch, ' ')
    return ' '.join(text.split())


def name_forms(value) -> set[str]:
    norm = norm_name(value)
    if not norm:
        return set()
    forms = {norm}
    head, _, tail = norm.partition(' ')
    if head in _LEGAL_FORMS and tail:
        forms.add(tail)
    return forms


def has_number(title, number) -> bool:
    """Whole-token match: invoice "55" must not match the title of "2026-455"."""
    if not number:
        return False
    pattern = (
        rf'(?<![{_NUMBER_CHARS}\-])'
        rf'{re.escape(str(number).casefold())}'
        rf'(?![{_NUMBER_CHARS}\-])'
    )
    return re.search(pattern, str(title or '').casefold()) is not None
