"""A segment's rules: checked on the way in, presented on the way out.

`validate_rules` is every 400 the builder can meet. It is written as the
person saving, so an organisation or account id must be one they may open,
and a person or product must be in their workspace. A missing id and a hidden
one read the same, so the error never confirms that a record exists.

`present_rules` is how stored rules read to someone: each id they cannot
open becomes `null`, and `labels` names only what they may see. The
compiler intersects ids with the viewer's own book the same way, so the
rules a viewer reads are exactly the rules they are evaluated by.
"""

import copy
from datetime import date

from services.accounts.models import User
from services.customers.models import Product
from services.customers.scoping import visible_accounts, visible_customers

from . import registry
from .models import MAX_CONDITIONS
from .registry import BOOLEAN, CHOICE, DATE, DAYS, OWNER, RECORD, TEXT, UNASSIGNED

MATCHES = ("all", "any")
MAX_DAYS = 3650
MAX_VALUES = 100
MAX_TEXT = 100
LEAF_KEYS = frozenset({"field", "op", "value"})
SHAPE = 'Rules are {"match": "all" | "any", "conditions": [...]}.'
LEAF = "A condition names a field and an operator."

NOT_OPEN = {
    "customer": "Not an organisation you can open.",
    "account": "Not an account you can open.",
    "product": "Not a product in your workspace.",
    "user": "Not a person in your workspace.",
}
LABEL_GROUPS = {
    "customer": "organisations",
    "account": "accounts",
    "product": "products",
    "user": "people",
}


class RuleError(ValueError):
    """A rule the registry refuses; its message is the 400's text."""


def leaves(rules):
    """Every condition, groups flattened, in order."""
    for condition in (rules or {}).get("conditions") or []:
        if isinstance(condition, dict) and "group" in condition:
            group = condition["group"] if isinstance(condition["group"], dict) else {}
            yield from group.get("conditions") or []
        else:
            yield condition


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ids_in(value):
    values = value if isinstance(value, list) else [value]
    return {part for part in values if _is_int(part)}


def _check_block(block, *, top):
    if not isinstance(block, dict) or set(block) != {"match", "conditions"}:
        raise RuleError(SHAPE)
    if block["match"] not in MATCHES:
        raise RuleError('"match" is "all" or "any".')
    if not isinstance(block["conditions"], list):
        raise RuleError('"conditions" is a list.')
    if not top and not block["conditions"]:
        raise RuleError("A group needs at least one condition.")
    for condition in block["conditions"]:
        if isinstance(condition, dict) and set(condition) == {"group"}:
            if not top:
                raise RuleError("Groups do not nest.")
            _check_block(condition["group"], top=False)
            continue
        if not (
            isinstance(condition, dict)
            and {"field", "op"} <= set(condition) <= LEAF_KEYS
            and isinstance(condition["field"], str)
            and isinstance(condition["op"], str)
        ):
            raise RuleError(LEAF)


def _scalar(field, value):
    if field.type == DATE:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise RuleError(f"{field.label}: a date as YYYY-MM-DD.") from None
    if field.type == DAYS:
        if not _is_int(value) or value < 0:
            raise RuleError(f"{field.label}: a whole number of days.")
        return value
    if not _is_number(value):
        raise RuleError(f"{field.label}: a number.")
    return value


def _member(field, value):
    if field.type == CHOICE and value not in field.choices:
        raise RuleError(f'{field.label}: "{value}" is not one of its values.')
    if field.type == TEXT and not (isinstance(value, str) and 0 < len(value) <= MAX_TEXT):
        raise RuleError(f"{field.label}: text of up to {MAX_TEXT} characters.")
    if field.type == BOOLEAN and not isinstance(value, bool):
        raise RuleError(f"{field.label}: true or false.")
    if field.type == OWNER and not (value in (UNASSIGNED, None) or _is_int(value)):
        raise RuleError(f'{field.label}: a person\'s id or "unassigned".')
    if field.type == RECORD and not (value is None or _is_int(value)):
        raise RuleError(f"{field.label}: an id.")


def _check_value(field, op, value):
    if op in ("is_empty", "is_not_empty"):
        if value is not None:
            raise RuleError(f"{field.label} {op.replace('_', ' ')} takes no value.")
        return
    if op in ("within_next", "within_last"):
        if not _is_int(value) or not 1 <= value <= MAX_DAYS:
            raise RuleError(f"{field.label}: a number of days from 1 to {MAX_DAYS}.")
        return
    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise RuleError(f"{field.label}: between takes two values.")
        low, high = (_scalar(field, part) for part in value)
        if low > high:
            raise RuleError(f"{field.label}: the first value must not be above the second.")
        return
    if op in ("gt", "lt"):
        _scalar(field, value)
        return
    if op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_VALUES:
            raise RuleError(f"{field.label}: choose from 1 to {MAX_VALUES} values.")
        for part in value:
            _member(field, part)
        return
    _member(field, value)


def _openable(record, ids, user):
    """`{id: name}` for the ids `user` may see named: organisations and
    accounts they may open, products and people in their own workspace."""
    if not ids:
        return {}
    if record == "customer":
        rows = visible_customers(user).filter(pk__in=ids)
    elif record == "account":
        rows = visible_accounts(user).filter(pk__in=ids)
    elif record == "product":
        rows = Product.objects.filter(organisation_id=user.organisation_id, pk__in=ids)
    else:
        rows = User.objects.filter(organisation_id=user.organisation_id, pk__in=ids)
    # SOC2:AUTH-02 named only when the reader may open it, or it is in their workspace
    return dict(rows.order_by().values_list("pk", "name").distinct())


def validate_rules(rules, kind, *, user):
    """`rules` checked against the registry for `kind`, as `user` writes
    them. Returns a clean copy; raises RuleError with the 400's text."""
    _check_block(rules, top=True)
    conditions = list(leaves(rules))
    if len(conditions) > MAX_CONDITIONS:
        raise RuleError(f"A segment can have at most {MAX_CONDITIONS} conditions.")
    attributes = registry.attributes_for(user.organisation_id, [c["field"] for c in conditions])
    wanted = {record: set() for record in NOT_OPEN}
    for condition in conditions:
        field = registry.resolve_any(kind, condition["field"], attributes)
        if field is None:
            raise RuleError(
                f'Unknown field "{condition["field"]}" for {registry.KIND_NOUNS[kind]}.'
            )
        if condition["op"] not in field.operators:
            raise RuleError(f'"{condition["op"]}" cannot be used with {field.label}.')
        _check_value(field, condition["op"], condition.get("value"))
        if field.record:
            wanted[field.record] |= _ids_in(condition.get("value"))
    for record, ids in wanted.items():
        # SOC2:AUTH-02 a missing id and one the writer cannot open read the same
        if ids and len(_openable(record, ids, user)) != len(ids):
            raise RuleError(NOT_OPEN[record])
    return copy.deepcopy(rules)


def _redact(value, openable):
    if isinstance(value, list):
        return [_redact(part, openable) for part in value]
    return None if _is_int(value) and value not in openable else value


def present_rules(rules, kind, *, user):
    """`(rules, labels)` as `user` may read them. Every id they cannot open
    (a person or product outside their workspace included) reads `null` and
    has no label; `labels` is `{"organisations", "accounts", "products",
    "people"}`, each `{"<id>": name}`. At most one query per record type the
    rules name."""
    rules = copy.deepcopy(rules or {})
    refs = {record: set() for record in LABEL_GROUPS}
    targets = []
    for condition in leaves(rules):
        field = registry.static_field(kind, condition.get("field", ""))
        if field is not None and field.record:
            refs[field.record] |= _ids_in(condition.get("value"))
            targets.append((condition, field.record))
    names = {record: _openable(record, ids, user) for record, ids in refs.items()}
    for condition, record in targets:
        condition["value"] = _redact(condition.get("value"), names[record])
    labels = {
        LABEL_GROUPS[record]: {str(pk): name for pk, name in found.items()}
        for record, found in names.items()
    }
    return rules, labels
