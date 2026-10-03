"""A segment's members, computed for whoever is looking.

Twice filtered, always: the rules run over the records the viewer may open
(`visible_records`), pins are added, exclusions removed, and visibility is
applied last. So a pin of a record the viewer cannot open is neither shown
nor named. `hidden_count` is the one thing a shared viewer learns about the
rest of the owner's members: how many there are.
"""

from dataclasses import dataclass, field

from django.db.models import Q

from services.customers.models import Account, Contact, Customer
from services.customers.scoping import visible_accounts, visible_children_q, visible_customers

from .compiler import compile_rules

MODELS = {"customer": Customer, "account": Account, "contact": Contact}


@dataclass(frozen=True)
class Draft:
    """An unsaved segment: what the builder's live preview evaluates."""

    kind: str
    rules: dict
    pinned_ids: list = field(default_factory=list)
    excluded_ids: list = field(default_factory=list)
    pk = None
    owner_id = None


def visible_records(kind, user):
    # SOC2:AUTH-02 a segment only ever holds records its viewer may open
    if kind == "customer":
        return visible_customers(user)
    if kind == "account":
        return visible_accounts(user)
    return Contact.objects.filter(visible_children_q(user))


def members_queryset(segment, user, *, today):
    """The segment's members as `user` may see them: the rules over their
    book, pins added, exclusions removed, visibility applied last."""
    compiled = compile_rules(segment.rules, segment.kind, user=user, today=today)
    matched = compiled.apply(visible_records(segment.kind, user))
    chosen = Q(pk__in=matched.values("pk")) | Q(pk__in=list(segment.pinned_ids or []))
    # SOC2:AUTH-02 visibility last: a pin the viewer cannot open is not theirs to see
    return (
        visible_records(segment.kind, user)
        .filter(chosen)
        .exclude(pk__in=list(segment.excluded_ids or []))
    )


def member_ids(segment, user, *, today):
    members = members_queryset(segment, user, today=today)
    return sorted(set(members.order_by().values_list("pk", flat=True)))


def hidden_count(segment, viewer, *, today):
    """How many of the owner's members `viewer` cannot open: one COUNT,
    never an id. Zero for the owner."""
    if segment.owner_id is None or segment.owner_id == viewer.pk:
        return 0
    owners = members_queryset(segment, segment.owner, today=today)
    # SOC2:AUTH-02 counted, never named: the owner's members the viewer cannot open
    return owners.exclude(pk__in=visible_records(segment.kind, viewer).values("pk")).count()


def openable_ids(kind, user, ids):
    """The ids among `ids` that `user` may open, sorted; no query for none."""
    if not ids:
        return []
    # SOC2:AUTH-02 only pins and exclusions the reader may open are shown
    rows = visible_records(kind, user).filter(pk__in=list(ids)).order_by()
    return sorted(set(rows.values_list("pk", flat=True)))
