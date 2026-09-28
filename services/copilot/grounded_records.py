"""The records an Ask digest quoted without citing them, as references.

An organisation page's digest quotes story items — an email's subject and first
line, a private note's title, a task — and counts records across the accounts
it covers. None of them is a citation the reader clicks, but a shared reply
can repeat any of them, so each is fixed on the reply as a reference
(`Message.grounded_records`) and a mentioned-only reader is checked against
it exactly as against `sources` (`views._reply_readable_by`): the company it
hangs off must be one they may open, an account by its own rule, and the
record must pass its own rule. References only — never a title or a body.
"""

#: The one shape a reference has, stored and checked.
RECORD_KEYS = frozenset({"type", "id", "company_type", "company_id"})
COMPANY_TYPES = ("customer", "account")


def record_ref(kind, ident, *, customer_id, account_id=None):
    """A reference to one record filed on an organisation (`account_id`
    None) or on one of its accounts."""
    if account_id is None:
        return {"type": kind, "id": ident, "company_type": "customer", "company_id": customer_id}
    return {"type": kind, "id": ident, "company_type": "account", "company_id": account_id}


def account_ref(account_id):
    """A reference to an account itself: the digest drew on it (its counts,
    its attention entries), so a reader must be able to open it."""
    return record_ref("account", account_id, customer_id=None, account_id=account_id)


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def well_formed_records(value):
    """A stored or built list of references, or None when it is anything
    else — a reply carrying one fails closed."""
    if not isinstance(value, list):
        return None
    for ref in value:
        if not isinstance(ref, dict) or set(ref) != RECORD_KEYS:
            return None
        if not isinstance(ref["type"], str) or ref["company_type"] not in COMPANY_TYPES:
            return None
        if not (_whole(ref["id"]) and _whole(ref["company_id"])):
            return None
    return value


def _key(ref):
    return (ref["type"], ref["id"], ref["company_type"], ref["company_id"])


def union_records(*lists):
    """The references in every list, once each, in one fixed order."""
    merged = {_key(ref): ref for refs in lists for ref in refs}
    return [merged[key] for key in sorted(merged)]
