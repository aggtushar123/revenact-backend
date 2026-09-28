"""The contacts list: which people a viewer sees under the page's filters,
and the summary line over all of them. GET /api/v1/contacts/ and Ask
Revenact on Contacts (copilot.contacts_grounding) both read people through
here, so an answer is grounded on exactly the list the page shows."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Contact
from .scoping import visible_accounts, visible_children_q, visible_customers

#: The roles the Contacts page counts as decision makers, the same set the
#: organisation page's People summary uses.
DECISION_ROLES = (
    Contact.Role.EXECUTIVE_SPONSOR,
    Contact.Role.DECISION_MAKER,
    Contact.Role.ECONOMIC_BUYER,
)


@dataclass(frozen=True)
class ContactFilters:
    search: str = ""
    customer: int | None = None
    account: int | None = None
    sentiment: str | None = None
    role: str | None = None


def _int_or_none(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_contact_filters(query: Mapping[str, str]) -> ContactFilters:
    """The list endpoint's query parameters; an unparseable value is
    dropped, never an error. An id that parses but isn't one the caller
    may open (negative, zero, or just not theirs) is kept and left to
    `filtered_contacts`'s own `.none()` handling below — matching nobody,
    same as the view did before this module existed."""
    sentiment = query.get("sentiment")
    role = query.get("role")
    return ContactFilters(
        search=(query.get("search") or "").strip(),
        customer=_int_or_none(query.get("customer") or query.get("company")),
        account=_int_or_none(query.get("account")),
        sentiment=sentiment if sentiment in Contact.Sentiment.values else None,
        role=role if role in Contact.Role.values else None,
    )


def filtered_contacts(user, filters: ContactFilters):
    """Every contact `user` may open, narrowed by `filters`, in name order.

    `.distinct()` because `account__customers__organisation=` fans out one
    row per matching linked Customer on the account."""
    # SOC2:AUTH-02 a contact follows its organisation's or account's visibility
    queryset = (
        Contact.objects.filter(visible_children_q(user))
        .select_related("customer", "account")
        .prefetch_related("account__customers")
        .distinct()
    )
    if filters.search:
        queryset = queryset.filter(
            Q(name__icontains=filters.search)
            | Q(email__icontains=filters.search)
            | Q(role__icontains=filters.search)
        )
    if filters.customer is not None:
        # SOC2:AUTH-02 an id the caller cannot open must not be confirmed to
        # exist by matching contacts on accounts also linked to it.
        if not visible_customers(user).filter(pk=filters.customer).exists():
            return queryset.none()
        queryset = queryset.filter(
            Q(customer_id=filters.customer) | Q(account__customers__id=filters.customer)
        )
    if filters.account is not None:
        # SOC2:AUTH-02 same as the organisation filter above
        if not visible_accounts(user).filter(pk=filters.account).exists():
            return queryset.none()
        queryset = queryset.filter(account_id=filters.account)
    if filters.sentiment:
        queryset = queryset.filter(sentiment=filters.sentiment)
    if filters.role:
        queryset = queryset.filter(role=filters.role)
    return queryset


def contacts_summary(queryset):
    """The summary line over the whole filtered set, not a page, in one
    query: `{total, positive, neutral, negative, decision_makers, active,
    growth_30d_pct}` (growth as ContactStatsView defines it)."""
    counted = Contact.objects.filter(pk__in=queryset.values("pk")).aggregate(
        total=Count("pk"),
        positive=Count("pk", filter=Q(sentiment=Contact.Sentiment.POSITIVE)),
        neutral=Count("pk", filter=Q(sentiment=Contact.Sentiment.NEUTRAL)),
        negative=Count("pk", filter=Q(sentiment=Contact.Sentiment.NEGATIVE)),
        decision_makers=Count("pk", filter=Q(role__in=DECISION_ROLES)),
        active=Count("pk", filter=Q(status=Contact.Status.ACTIVE)),
        total_30d_ago=Count("pk", filter=Q(created_at__lte=timezone.now() - timedelta(days=30))),
    )
    baseline = counted.pop("total_30d_ago")
    counted["growth_30d_pct"] = (
        round((counted["total"] - baseline) / baseline * 100, 1) if baseline else None
    )
    return counted
