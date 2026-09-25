"""What needs a person first: the Dashboard Overview's "Needs attention" list.

Five kinds of item, each built from rules other screens already own, so the
list can never disagree with the page an item comes from:

- **renewal** — renews inside 90 days (or is overdue) and is not Good.
- **risk** — the Triage score (`services.customers.triage`) at or above its
  action threshold, from exactly the inputs the Health serializer uses.
- **going_quiet** — the Activity dashboard's own going-dark list.
- **support** — unresolved High/Critical tickets the viewer's department may read.
- **anomaly** — live anomalies with evidence the viewer may read on a company
  in their (filtered) book.

Every item is scored `at_stake × urgency`: ARR in the organisation's currency
times a 0.25–1.0 urgency whose formula per kind is the spec amendment's
(react-ts-app/docs/superpowers/specs/2026-09-23-dashboard-redesign-design.md).

Twice filtered: companies come from `live_customers` narrowed by the bar's
filters, and each record is read under its own rule on top (tickets by
department, anomaly evidence by `visible_evidence`). No record text reaches an
item: reasons are built from fields, and the anomaly title is the one the
Anomalies page already shows.

Snoozes are not applied here — `build_items` returns every candidate and
`worse` tells the caller whether a snoozed item has since got worse.
"""

import re
from collections import defaultdict
from datetime import timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from services.anomalies.models import Anomaly
from services.anomalies.views import title_for, visible_evidence
from services.customers import forecast
from services.customers.activity_tracking import dark_accounts
from services.customers.contact import last_contact_by_customer
from services.customers.models import HealthSnapshot, Ticket
from services.customers.personal import visible_tickets
from services.customers.scoping import sees_everything, visible_children_q
from services.customers.triage import ACTION_THRESHOLD, SEVERITY, triage
from services.customers.views import CustomerHealthView
from services.fx_rates.conversion import convert_to_org_currency, rates_for

#: Renewals further out than this are not yet an attention item.
RENEWAL_WINDOW_DAYS = 90

#: Priorities that make a ticket an attention item.
SUPPORT_PRIORITIES = (Ticket.Priority.CRITICAL, Ticket.Priority.HIGH)

#: The urgency floor and ceiling every kind shares.
FLOOR, CEILING = 0.25, 1.0


def _linear(value, start, end, at_start, at_end):
    """`at_start` at or before `start`, `at_end` at or after `end`, a straight
    line between, rounded to three places."""
    if value <= start:
        return round(at_start, 3)
    if value >= end:
        return round(at_end, 3)
    return round(at_start + (at_end - at_start) * (value - start) / (end - start), 3)


def urgency_for_days_to(days):
    """Renewal and risk: overdue or ≤ 14 days → 1.0, down to 0.25 at 90 days.
    No renewal date at all → 0.5."""
    if days is None:
        return 0.5
    return _linear(days, 14, 90, CEILING, FLOOR)


def urgency_quiet(days_since):
    """Going quiet: never contacted → 1.0; 0.25 at 60 days, up to 1.0 at 120."""
    if days_since is None:
        return CEILING
    return _linear(days_since, 60, 120, FLOOR, CEILING)


def urgency_ticket_age(days):
    """Support: the oldest matching ticket's age; 0.25 new, 1.0 at 14 days."""
    return _linear(days, 0, 14, FLOOR, CEILING)


def urgency_anomaly_age(days):
    """Anomaly: days since first seen; ≤ 7 → 1.0, down to 0.25 at 90 days."""
    return _linear(days, 7, 90, CEILING, FLOOR)


def filtered_customers(user, params, *, history=True):
    """The viewer's live book narrowed by the bar's filters (`forecast`'s own
    parsing, which already annotates the health inputs), with the health
    history the Triage score reads.

    The history window is the Health view's default, not a shorter one: the
    score only reads the last three snapshots, but a customer whose snapshots
    stopped months ago would otherwise get a shorter trail here than on the
    Health page, and the two scores would differ.

    Snapshots load lean — only the fields the score reads — and not at all
    when `history` is False (no risk items being built)."""
    customers = forecast.filtered_customers(user, params)
    if not history:
        return customers
    earliest = timezone.localdate() - timedelta(days=31 * CustomerHealthView.DEFAULT_HISTORY_MONTHS)
    snapshots = (
        HealthSnapshot.objects.filter(captured_on__gte=earliest)
        .only("id", "customer_id", "captured_on", "health_score")
        .order_by("captured_on")
    )
    return customers.prefetch_related(Prefetch("health_snapshots", queryset=snapshots))


def _plural(count, word, plural=None):
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _money(customer, organisation, rates):
    """ARR in the organisation's currency as a float, or None when no rate is
    configured for the customer's currency."""
    converted = convert_to_org_currency(
        customer.arr_billed_at_account, customer.currency, organisation, rates=rates
    )
    return None if converted is None else round(float(converted), 2)


def _item(
    kind,
    ident,
    title,
    reason,
    arr,
    urgency,
    fingerprint,
    *,
    customer_id,
    companies=(),
    unknown=None,
):
    """One item. Unconvertible ARR counts as nothing at stake — the item
    still appears, and its reason says so. `unknown` overrides "arr is None"
    for an item summed over several companies, some of them unconvertible."""
    if unknown is None:
        unknown = arr is None
    at_stake = 0.0 if arr is None else arr
    if unknown:
        reason = f"{reason} · ARR unknown"
    return {
        "key": f"{kind}:{ident}",
        "kind": kind,
        "title": title,
        "reason": reason,
        "at_stake": at_stake,
        "urgency": urgency,
        "score": round(at_stake * urgency, 2),
        "customer_id": customer_id,
        "companies": list(companies),
        "fingerprint": {**fingerprint, "arr": at_stake},
    }


def _customer_item(kind, customer, reason, arr, urgency, fingerprint):
    return _item(
        kind, customer.pk, customer.name, reason, arr, urgency, fingerprint, customer_id=customer.pk
    )


def _renewal_items(customers, money, today):
    items = []
    for customer in customers:
        if customer.renewal_date is None or customer.health_category == "good":
            continue
        days = (customer.renewal_date - today).days
        if days > RENEWAL_WINDOW_DAYS:
            continue
        health = customer.health_category
        when = (
            f"renewal {_plural(-days, 'day')} overdue"
            if days < 0
            else f"renews in {_plural(days, 'day')}"
        )
        items.append(
            _customer_item(
                "renewal",
                customer,
                f"{when} · health {health.capitalize()}",
                money[customer.pk],
                urgency_for_days_to(days),
                {
                    "overdue": days < 0,
                    "health": health,
                    "renewal_date": customer.renewal_date.isoformat(),
                },
            )
        )
    return items


def _risk_items(customers, money, today):
    """The Health serializer's own call (`CustomerHealthRowSerializer._triage`):
    same fields, same history (the prefetched snapshots, oldest first)."""
    items = []
    for customer in customers:
        result = triage(
            health_category=customer.health_category,
            csm_pulse=customer.csm_pulse_score,
            ai_pulse=customer.ai_pulse_value,
            renewal_date=customer.renewal_date,
            history=[snapshot.health_category for snapshot in customer.health_snapshots.all()],
            today=today,
        )
        if result.score < ACTION_THRESHOLD:
            continue
        items.append(
            _customer_item(
                "risk",
                customer,
                f"risk {result.score} · {result.factors[0]['label']}",
                money[customer.pk],
                urgency_for_days_to(result.days_to_renewal),
                {"score": result.score},
            )
        )
    return items


def _going_quiet_items(customers, money, today, organisation, rates):
    by_id = {customer.pk: customer for customer in customers}
    latest = last_contact_by_customer(list(by_id)) if by_id else {}
    items = []
    for row in dark_accounts(customers, latest, today, organisation, rates):
        days = row["days_since_contact"]
        items.append(
            _customer_item(
                "going_quiet",
                by_id[row["id"]],
                "never contacted" if days is None else f"no contact in {_plural(days, 'day')}",
                money[row["id"]],
                urgency_quiet(days),
                {"last_contact": row["last_contact"]},
            )
        )
    return items


def _support_items(user, customers, money, today):
    """Tickets read under the department rule, then grouped per company. A
    ticket on an account counts for each of that account's companies that
    are in the (filtered) book."""
    by_id = {customer.pk: customer for customer in customers}
    if not by_id:
        return []
    ids = list(by_id)
    tickets = (
        visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())
        .filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .distinct()
        .prefetch_related("account__customers")
    )
    ages, open_ids = defaultdict(list), defaultdict(list)
    for ticket in tickets:
        age = max(0, (today - ticket.opened_at).days)
        targets = {ticket.customer_id} if ticket.customer_id else set()
        if ticket.account_id:
            targets |= {customer.pk for customer in ticket.account.customers.all()}
        for customer_id in targets & by_id.keys():
            ages[customer_id].append(age)
            open_ids[customer_id].append(ticket.pk)

    items = []
    for customer_id, customer_ages in ages.items():
        count, oldest = len(customer_ages), max(customer_ages)
        items.append(
            _customer_item(
                "support",
                by_id[customer_id],
                f"{_plural(count, 'open High/Critical ticket')} · oldest {_plural(oldest, 'day')}",
                money[customer_id],
                urgency_ticket_age(oldest),
                {"count": count, "open_ids": sorted(open_ids[customer_id])},
            )
        )
    return items


def _anomaly_items(user, customers, money, today, organisation):
    """Live anomalies whose readable evidence lands on a company in the
    book. One `visible_evidence` read for the whole org rather than one per
    anomaly: same rule, one set of queries."""
    by_id = {customer.pk: customer for customer in customers}
    if not by_id:
        return []
    rows = (
        visible_evidence(organisation, user)
        .filter(anomaly__status=Anomaly.Status.LIVE)
        .select_related("anomaly")
        .prefetch_related("account__customers")
    )
    anomalies, behind = {}, defaultdict(set)
    for row in rows:
        targets = {row.customer_id} if row.customer_id else set()
        if row.account_id:
            targets |= {customer.pk for customer in row.account.customers.all()}
        hits = targets & by_id.keys()
        if hits:
            anomalies[row.anomaly_id] = row.anomaly
            behind[row.anomaly_id] |= hits

    # A model-written title can name a company outside this viewer's book, or
    # count companies across the whole org. Only someone who sees every
    # account gets it as written; everyone else gets one built from fields.
    sees_all = sees_everything(user)
    items = []
    for anomaly_id, anomaly in anomalies.items():
        companies = sorted((by_id[pk] for pk in behind[anomaly_id]), key=lambda c: (c.name, c.pk))
        amounts = [money[company.pk] for company in companies]
        known = [amount for amount in amounts if amount is not None]
        first_seen = timezone.localtime(anomaly.first_seen_at).date()
        age = max(0, (today - first_seen).days)
        item = _item(
            "anomaly",
            anomaly.pk,
            title_for(anomaly.title, len(companies), sees_all=sees_all),
            f"{_plural(len(companies), 'company', 'companies')} · first seen "
            f"{_plural(age, 'day')} ago",
            round(sum(known), 2),
            urgency_anomaly_age(age),
            {"companies": len(companies)},
            customer_id=None,
            companies=[{"id": company.pk, "name": company.name} for company in companies],
            # An unconvertible company counts as 0, like everywhere else, and
            # the reason says the total is short.
            unknown=len(known) < len(amounts),
        )
        items.append(item)
    return items


#: Every kind, in the order the list builds them.
KINDS = ("renewal", "risk", "going_quiet", "support", "anomaly")


def build_items(user, params, *, today, kinds=KINDS, customers=None):
    """Every candidate item for this viewer and these filters, unsorted and
    not yet snooze-filtered. `kinds` restricts the build to those kinds
    only — the snooze endpoint checks one key without building the rest;
    narrowing to one company goes through the filters' own `customer`
    param, so it stays inside the viewer's visible book.

    `customers`, when given, is `filtered_customers(user, params)` already
    loaded — with its history when "risk" is among `kinds` — so a caller that
    has the book does not load it again."""
    organisation = user.organisation
    rates = rates_for(organisation)
    if customers is None:
        customers = list(filtered_customers(user, params, history="risk" in kinds))
    money = {customer.pk: _money(customer, organisation, rates) for customer in customers}
    builders = {
        "renewal": lambda: _renewal_items(customers, money, today),
        "risk": lambda: _risk_items(customers, money, today),
        "going_quiet": lambda: _going_quiet_items(customers, money, today, organisation, rates),
        "support": lambda: _support_items(user, customers, money, today),
        "anomaly": lambda: _anomaly_items(user, customers, money, today, organisation),
    }
    return [item for kind in KINDS if kind in kinds for item in builders[kind]()]


#: `<kind>:<id>` — the only shape a key ever has.
KEY_PATTERN = re.compile(rf"^({'|'.join(KINDS)}):([1-9][0-9]*)$")


def current_item(user, key, *, today=None):
    """The viewer's item under `key` right now, or None — the one check both
    snoozing and the dashboard's Ask Revenact apply to a key a client sends.

    Only the key's own kind is built, and for a company's kind only that
    company — through the filters' `customer` param, so it still has to be in
    the viewer's visible book. An anomaly key builds the anomaly kind over the
    whole book, since its fingerprint counts companies. Anything that is not
    a well-formed key, including a non-string, is None."""
    if not isinstance(key, str):
        return None
    match = KEY_PATTERN.match(key)
    if match is None:
        return None
    kind, ident = match.groups()
    params = {} if kind == "anomaly" else {"customer": ident}
    items = build_items(user, params, today=today or timezone.localdate(), kinds=(kind,))
    return next((item for item in items if item["key"] == key), None)


def worse(kind, stored, current):
    """Has an item got worse since it was snoozed? `stored` and `current`
    are its fingerprints then and now. Any one measure worsening is enough.

    Fingerprints hold facts, never a count of days: a day passing is not the
    item getting worse, so a snooze (or Done) survives the calendar. A key
    missing from either side — a fingerprint stored under an older shape —
    counts as not worse, and nothing here raises."""
    if kind not in WORSE or not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    return _rose(stored, current, "arr") or WORSE[kind](stored, current)


def _rose(old, new, field, measure=lambda value: value):
    """Did `field` go up from `old` to `new`? False when either side lacks
    it or can't be compared."""
    try:
        return measure(new[field]) > measure(old[field])
    except (KeyError, TypeError):
        return False


def _changed(old, new, field):
    """Is `field` present on both sides and different? A missing key (an
    older fingerprint shape) is not a change."""
    return field in old and field in new and old[field] != new[field]


def _new_ids(old, new, field):
    """Does `new[field]` hold an id `old[field]` didn't? False when either
    side lacks it or isn't a list of ids."""
    try:
        return bool(set(new[field]) - set(old[field]))
    except (KeyError, TypeError):
        return False


def _severity(health):
    return SEVERITY.get(health, 0)


#: Per kind, whether the non-money part of the fingerprint got worse.
#: A new episode counts as worse too, so one Done never outlives the episode
#: it was set on: a different renewal date (a new cycle), a different last
#: contact (a new silence), a ticket that wasn't open before. The silence
#: growing is not a change — it is why a going-quiet item exists.
WORSE = {
    "renewal": lambda old, new: (
        _rose(old, new, "overdue")
        or _rose(old, new, "health", _severity)
        or _changed(old, new, "renewal_date")
    ),
    "risk": lambda old, new: _rose(old, new, "score"),
    "going_quiet": lambda old, new: _changed(old, new, "last_contact"),
    "support": lambda old, new: _rose(old, new, "count") or _new_ids(old, new, "open_ids"),
    "anomaly": lambda old, new: _rose(old, new, "companies"),
}
