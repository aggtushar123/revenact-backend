"""What counts as contact with a customer — defined once.

Two things read this and must never disagree: the health rubric's Customer
Touch component (`Customer.health_inputs`, via the annotation below) and the
Activity Tracking dashboard's coverage and cadence (`last_contact_by_customer`).
Until they shared this module the rubric counted logged Activities only while
the dashboard counted every kind of contact, and the cadence chart carried a
footnote admitting the two could differ. A CSM who emailed a customer every
week could watch its health score decay for "no touch".

`TOUCH_SOURCES` is the rule. The two functions are renderings of it — one in
SQL for list pages, one in Python for id lists — and test_contact.py pins them
to the same answer.

Tickets are deliberately not here: a customer opening a ticket is contact
*from* them, not evidence that anyone reached out.
"""

from django.db.models import DateField, DateTimeField, OuterRef, Q, Subquery
from django.db.models.functions import Greatest, TruncDate

from .models import Activity, CalendarEvent, Call, Email, Note

#: source key -> (model, date field, label). The date field is what "when"
#: means for that source; two of them are timestamps, the rest plain dates.
TOUCH_SOURCES = {
    "activities": (Activity, "occurred_at", "Activities"),
    "calls": (Call, "occurred_at", "Calls"),
    "emails": (Email, "sent_at", "Emails"),
    "notes": (Note, "logged_at", "Notes"),
    "meetings": (CalendarEvent, "event_date", "Meetings"),
}


def _is_timestamp(model, field):
    return isinstance(model._meta.get_field(field), DateTimeField)


def parent_q(ids):
    """Rows that belong to any of these customers, directly or via an Account.

    A touch on a division is a touch on that company: the same direction of
    travel record visibility already uses, and the reason a coverage number
    computed customer-only would report a worked account as neglected.
    """
    return Q(customer_id__in=ids) | Q(account__customers__id__in=ids)


def last_contact_by_customer(ids):
    """The most recent contact of any kind, per customer id, as a date.

    One query per source rather than a union: the sources have different date
    fields and different parent shapes, and five small aggregates are cheaper
    to read than one clever query nobody can modify later.
    """
    from django.db.models import Max

    latest = {}
    for model, field, _label in TOUCH_SOURCES.values():
        rows = (
            model.objects.filter(parent_q(ids))
            .order_by()
            .values("customer_id", "account__customers__id")
            .annotate(last=Max(field))
        )
        for row in rows:
            customer_id = row["customer_id"] or row["account__customers__id"]
            if customer_id is None or row["last"] is None:
                continue
            when = row["last"]
            # Timestamps become dates in UTC — the same day TruncDate picks in
            # the annotation below, because TIME_ZONE is UTC.
            when = when.date() if hasattr(when, "date") else when
            if customer_id not in latest or when > latest[customer_id]:
                latest[customer_id] = when
    return latest


def _newest_date_subquery(model, field):
    """The newest date for one source, correlated to the outer customer."""
    parent = Q(customer=OuterRef("pk")) | Q(account__customers=OuterRef("pk"))
    rows = model.objects.filter(parent).order_by()
    if _is_timestamp(model, field):
        rows = rows.annotate(_day=TruncDate(field)).order_by("-_day").values("_day")
    else:
        rows = rows.order_by(f"-{field}").values(field)
    return Subquery(rows[:1], output_field=DateField())


def last_contact_annotation():
    """`last_contact_by_customer` as one expression, for `with_health_inputs`.

    One correlated subquery per source, combined with GREATEST — which on
    PostgreSQL (this project's database) skips NULLs, so a customer with
    emails but no meetings gets the newest email rather than nothing. On an
    engine where GREATEST returns NULL for any NULL input this would need a
    COALESCE per source; test_contact.py would fail loudly rather than let
    every score quietly lose its touch component.
    """
    return Greatest(
        *(_newest_date_subquery(model, field) for model, field, _ in TOUCH_SOURCES.values())
    )


def last_contact_by_account(ids):
    """`last_contact_by_customer` for accounts: the newest contact of any
    kind logged *on the account itself*, per account id, as a date."""
    from django.db.models import Max

    latest = {}
    for model, field, _label in TOUCH_SOURCES.values():
        rows = (
            model.objects.filter(account_id__in=ids)
            .order_by()
            .values("account_id")
            .annotate(last=Max(field))
        )
        for row in rows:
            if row["last"] is None:
                continue
            when = row["last"]
            when = when.date() if hasattr(when, "date") else when
            if row["account_id"] not in latest or when > latest[row["account_id"]]:
                latest[row["account_id"]] = when
    return latest


def _newest_account_date_subquery(model, field):
    rows = model.objects.filter(account=OuterRef("pk")).order_by()
    if _is_timestamp(model, field):
        rows = rows.annotate(_day=TruncDate(field)).order_by("-_day").values("_day")
    else:
        rows = rows.order_by(f"-{field}").values(field)
    return Subquery(rows[:1], output_field=DateField())


def last_account_contact_annotation():
    """`last_contact_by_account` as one expression, for `with_pulse_inputs`."""
    return Greatest(
        *(_newest_account_date_subquery(model, field) for model, field, _ in TOUCH_SOURCES.values())
    )
