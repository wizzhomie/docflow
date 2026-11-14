"""What every CRM connector must be able to do.

The application never talks to a CRM vendor directly: it asks the active
connector. A connector owns its own transport (Uspacy JWT, Bitrix24 incoming
webhook) and returns the same small dictionaries, so the review flow and the
tests stay vendor-neutral.
"""


class Connector:
    """Interface only; subclasses implement the four operations below."""

    name = 'base'
    display_name = 'Base connector'

    def configured(self) -> bool:
        raise NotImplementedError

    def existing_deal(self, fields: dict) -> dict | None:
        """Return an already-created deal for this invoice, or None."""
        raise NotImplementedError

    def ensure_company(self, fields: dict) -> dict | None:
        """Find or create the supplier company; may fill its empty fields."""
        raise NotImplementedError

    def ensure_contact(self, fields: dict, company: dict | None) -> dict | None:
        """Find or create the contact person, linked to the company."""
        raise NotImplementedError

    def create_deal(self, *, title: str, fields: dict, company, contact, doc_id) -> dict:
        """Create the deal and link company/contact; return {'id', 'title'}."""
        raise NotImplementedError
