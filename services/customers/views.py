from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from services.fx_rates.conversion import convert_to_org_currency

from .models import Account, Contact, Customer, Opportunity, Risk
from .serializers import (
    AccountSerializer,
    ActivitySerializer,
    CalendarEventSerializer,
    ContactSerializer,
    CustomerSerializer,
    EmailSerializer,
    NoteSerializer,
    OpportunitySerializer,
    RiskSerializer,
    TaskSerializer,
    TicketSerializer,
)


class CustomerListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/ — scoped to the caller's own
    organisation (tenant). Any authenticated user (admin or CSM) can list
    and add — unlike User Management, there's no admin-only gate here.

    GET supports `?search=` — matches against name (substring, case-
    insensitive) or Revenact ID (the row's own `id`, also substring —
    `id` is cast to text via `Cast` rather than relying on a database's
    own icontains-on-integer behaviour, which isn't portable). There's no
    separate "External ID" field in the schema yet, so that part of the
    frontend's search placeholder isn't wired to anything real.

    GET also supports `?renewal_within=<days>` — customers whose
    `renewal_date` is on or before today+<days>, ordered soonest/most-
    overdue-first instead of the model's default name ordering. No lower
    bound: an already-overdue renewal_date (the CSM hasn't updated it
    yet) is *more* urgent, not less, so it's included rather than
    filtered out. Excludes already-churned customers (renewal is moot
    for those) and anything with no `renewal_date` set. Powers the
    Organizations page's "Renewal" card/popover. A non-integer value is
    ignored rather than raising an error.

    Archived customers (`is_archived=True`) never appear here — soft-
    hidden, same as from the stats endpoint below. They're still
    reachable directly via the detail endpoint (not deleted), and PATCH
    `is_archived` on it to unarchive; there's just no "show archived"
    view yet."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Customer.objects.filter(
            organisation=self.request.user.organisation, is_archived=False
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.annotate(id_as_text=Cast("id", CharField())).filter(
                Q(name__icontains=search) | Q(id_as_text__icontains=search)
            )

        renewal_within = self.request.query_params.get("renewal_within")
        if renewal_within is not None:
            try:
                days = int(renewal_within)
            except ValueError:
                days = None
            if days is not None:
                deadline = timezone.localdate() + timedelta(days=days)
                queryset = (
                    queryset.exclude(lifecycle_stage=Customer.LifecycleStage.CHURN)
                    .filter(renewal_date__isnull=False, renewal_date__lte=deadline)
                    .order_by("renewal_date")
                )

        return queryset


class CustomerStatsView(views.APIView):
    """GET /api/v1/customers/stats/ — aggregate rollups for the
    Organizations page's MetricsPanel (Health / NPS / Lifecycle Stages
    sections), scoped to the caller's own organisation.

    Every customer counts here, churned ones included — "churn" is
    itself one of the lifecycle buckets below, unlike ?renewal_within=
    (on the list endpoint), which excludes them because renewal is moot
    for an already-churned customer.

    There's no stored MRR field (see Customer model's docstring on why —
    financials are stored independently, not derived, except this one:
    MRR has no independent meaning of its own here, it's purely
    `arr_billed_at_account / 12`) — computed the same way here as the
    frontend already does it elsewhere (features/customers/mapToOrgRow.ts).

    Each customer's own `arr_billed_at_account` is in *its own*
    `currency` (see that model's own docstring — independent of
    Organisation.currency), so it's converted into the org's own
    currency (via services.fx_rates.conversion.convert_to_org_currency)
    before being added to any bucket sum — these buckets are one
    tenant-wide total and can't meaningfully mix currencies. A customer
    whose currency has no configured FxRate is still counted (`count`),
    but excluded from every `mrr`/`arr` sum rather than having its
    unconverted amount silently treated as if it were already in the
    org's currency — `unconverted_count` in the response is exactly how
    many customers that happened to, so the frontend can show a caveat
    instead of a silently-too-low total.

    NPS: a customer with no `nps_score` set is excluded from the
    promoters/passives/detractors breakdown and the score's denominator
    (there's nothing to bucket it as) rather than silently counted as a
    passive.

    This aggregates in Python over the caller's own customers rather
    than via SQL-side conditional aggregation, because `health_category`
    is a derived Python property (from `health_score`), not a real
    column to GROUP BY — see Customer.health_category. Fine at the scale
    of one tenant's own customer list.

    Archived customers are excluded, same as from the list endpoint."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        customers = Customer.objects.filter(organisation=organisation, is_archived=False)

        health = {
            cat: {"count": 0, "mrr": 0.0, "arr": 0.0} for cat in Customer.HealthCategory.values
        }
        lifecycle = {
            stage: {"count": 0, "mrr": 0.0, "arr": 0.0} for stage in Customer.LifecycleStage.values
        }
        promoters = passives = detractors = 0
        scored = 0
        unconverted_count = 0

        for customer in customers:
            health_bucket = health[customer.health_category]
            health_bucket["count"] += 1
            lifecycle_bucket = lifecycle[customer.lifecycle_stage]
            lifecycle_bucket["count"] += 1

            converted = convert_to_org_currency(
                customer.arr_billed_at_account, customer.currency, organisation
            )
            if converted is None:
                unconverted_count += 1
            else:
                arr = float(converted)
                mrr = arr / 12
                health_bucket["mrr"] += mrr
                health_bucket["arr"] += arr
                lifecycle_bucket["mrr"] += mrr
                lifecycle_bucket["arr"] += arr

            if customer.nps_score is not None:
                scored += 1
                if customer.nps_score > 0:
                    promoters += 1
                elif customer.nps_score == 0:
                    passives += 1
                else:
                    detractors += 1

        for bucket in (*health.values(), *lifecycle.values()):
            bucket["mrr"] = round(bucket["mrr"], 2)
            bucket["arr"] = round(bucket["arr"], 2)

        nps_score = round((promoters - detractors) / scored * 100) if scored else 0

        return Response(
            {
                "health": health,
                "nps": {
                    "promoters": promoters,
                    "passives": passives,
                    "detractors": detractors,
                    "score": nps_score,
                },
                "lifecycle": lifecycle,
                "unconverted_count": unconverted_count,
            }
        )


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<id>/ — scoped to the caller's own
    organisation. 404, not 403, for a customer outside that scope."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Customer.objects.filter(organisation=self.request.user.organisation)


class AccountListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/ — every Account
    linked to one Customer (GET, one of possibly several it's linked to
    — see Account's own docstring), or adds a new one to it (POST),
    scoped to the caller's own organisation. The URL's own customer_id
    is never *removed* by a POST/PATCH through this URL — see
    perform_create below and AccountSerializer's own `customer_ids`
    field for adding/removing any *other* linked organisations.

    404 (not 403) for a customer_id outside the caller's organisation or
    that doesn't exist, same convention as CustomerDetailView — checked
    once up front via get_object_or_404 rather than left to fall out of
    an empty queryset, so a real customer in another org 404s the same
    way a nonexistent id does, instead of silently returning `[]` either
    way and leaving the two indistinguishable to the frontend.

    Add/Edit Account (this view's POST + AccountDetailView's PATCH below)
    covers identity, ownership, lifecycle stage, and renewal date — the
    fields an account genuinely has going in. Health/pulse/AI-pulse/NPS/
    CSAT/ARR are technically writable via AccountSerializer too (not
    restricted at the API layer, same as CustomerSerializer) but the
    Add/Edit Account UI never sends them — meant to sync from other
    systems later, same reasoning as Customer's own Add/Edit form."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )

    def get_queryset(self):
        return self.get_customer().accounts.all()

    def perform_create(self, serializer):
        # `customer_ids` (if the client sent it) already set whatever
        # full membership it asked for via the serializer's own
        # `source="customers"` — this always additionally links the
        # URL's own customer, so "Add Account" from this URL never
        # silently creates an account that isn't actually linked to the
        # Customer it was added under.
        customer = self.get_customer()
        account = serializer.save()
        account.customers.add(customer)


class AccountDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<customer_id>/accounts/<id>/ — scoped
    to the caller's own organisation and the given customer_id (one of
    possibly several the Account is linked to). 404, not 403, for either
    id outside that scope. See AccountListCreateView's docstring for
    what's actually editable — including AccountSerializer's own
    `customer_ids`, which a PATCH through this URL can use to add/remove
    *any* linked organisation, this one included (down to the
    "at least one" floor validate_customer_ids enforces)."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Account.objects.filter(
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )


class AccountListView(generics.ListAPIView):
    """GET /api/v1/accounts/ — every Account across every Customer the
    caller's own organisation owns. Powers the standalone Accounts page
    (react-ts-app's /accounts/list) — the one place an Account is
    browsed independent of which Customer it belongs to, same reasoning
    as ContactListView.

    GET-only — unlike OpportunityListView/RiskListView, there's no
    matching POST here. Account has only one possible parent (a
    Customer, not the two-tier Customer-or-Account shape those two
    have), so "Add Account" from the standalone list already knows
    exactly which nested endpoint to POST to once a company is picked
    (/customers/<customer_id>/accounts/, i.e. AccountListCreateView
    above) — same reasoning ContactListView's own standalone "Add
    Contact" already follows, no separate flat create endpoint needed.

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE, same
    reasoning as ContactListView (this can span every account the
    tenant has, unlike the nested per-Customer list above, which stays
    unpaginated since it's normally small).

    `?search=` matches name (substring, case-insensitive), same
    convention as CustomerListCreateView/ContactListView's own search.
    `?company=<customer_id>` filters to one company's own Accounts."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` because `customers__organisation=` fans out one
        # row per matching linked Customer — an Account linked to two+
        # Customers in this same organisation would otherwise appear
        # once per match instead of once overall.
        queryset = (
            Account.objects.filter(customers__organisation=organisation)
            .prefetch_related("customers")
            .select_related("owner")
            .distinct()
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)

        company = self.request.query_params.get("company")
        if company:
            try:
                company_id = int(company)
            except ValueError:
                company_id = None
            if company_id is not None:
                queryset = queryset.filter(customers__id=company_id)

        return queryset


class AccountStatsView(views.APIView):
    """GET /api/v1/accounts/stats/ — aggregate rollups for the standalone
    Accounts page's own MetricsPanel (Health / NPS / Lifecycle Stages
    sections), scoped to the caller's own organisation. Same shape and
    same reasoning as CustomerStatsView — see that view's own docstring
    for the health/lifecycle bucketing and NPS-denominator rules, all
    identical here.

    Unlike CustomerStatsView, `account.arr` is used as-is, with no FX
    conversion step — Account has no `currency` field of its own (see
    that model's docstring: it can belong to more than one Customer, so
    an independent per-Account currency has no clean meaning). Every
    Account's `arr` is always treated as already being in the org's own
    currency. This is a real, explicit scope boundary of the per-
    Customer-currency feature, not an oversight.

    Every Account under the caller's org counts here — Account has no
    `is_archived` field to exclude anything by (see the model's own
    docstring: there's no "hide this account" concept yet). MRR is
    `arr / 12`, computed the same way as CustomerStatsView's own (and
    features/customers/mapToAccountRow.ts's own) — Account doesn't
    store MRR independently either.

    Aggregates in Python over the caller's own accounts, same reasoning
    as CustomerStatsView: `health_category` is a derived Python
    property, not a real column to GROUP BY. Fine at the scale of one
    tenant's own account list."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # `.distinct()` for the same "an Account linked to two+ Customers
        # in this organisation would otherwise fan out" reason as
        # AccountListView's own — critical here, since this loop counts
        # each row once towards the health/lifecycle/NPS buckets; a
        # duplicate row would double-count that one Account.
        accounts = Account.objects.filter(
            customers__organisation=request.user.organisation
        ).distinct()

        health = {
            cat: {"count": 0, "mrr": 0.0, "arr": 0.0} for cat in Customer.HealthCategory.values
        }
        lifecycle = {
            stage: {"count": 0, "mrr": 0.0, "arr": 0.0} for stage in Customer.LifecycleStage.values
        }
        promoters = passives = detractors = 0
        scored = 0

        for account in accounts:
            arr = float(account.arr)
            mrr = arr / 12

            health_bucket = health[account.health_category]
            health_bucket["count"] += 1
            health_bucket["mrr"] += mrr
            health_bucket["arr"] += arr

            lifecycle_bucket = lifecycle[account.lifecycle_stage]
            lifecycle_bucket["count"] += 1
            lifecycle_bucket["mrr"] += mrr
            lifecycle_bucket["arr"] += arr

            if account.nps_score is not None:
                scored += 1
                if account.nps_score > 0:
                    promoters += 1
                elif account.nps_score == 0:
                    passives += 1
                else:
                    detractors += 1

        for bucket in (*health.values(), *lifecycle.values()):
            bucket["mrr"] = round(bucket["mrr"], 2)
            bucket["arr"] = round(bucket["arr"], 2)

        nps_score = round((promoters - detractors) / scored * 100) if scored else 0

        return Response(
            {
                "health": health,
                "nps": {
                    "promoters": promoters,
                    "passives": passives,
                    "detractors": detractors,
                    "score": nps_score,
                },
                "lifecycle": lifecycle,
            }
        )


class CustomerActivityListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/activities/ — every
    organization-level Activity for one Customer, scoped to the
    caller's own organisation. 404 (not an empty list) for a
    customer_id outside that scope, same convention as
    AccountListCreateView. Powers ActivityFeed's "Activities" filter on
    the Organization Details page's General tab."""

    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.activities.all()


class AccountActivityListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/activities/
    — every account-level Activity for one Account, scoped to both its
    customer_id and the caller's own organisation. 404 for either
    mismatch, same reasoning as AccountDetailView. Powers ActivityFeed's
    "Activities" filter on the standalone Account page — same component
    as the Customer-scoped view above, reading a different scope."""

    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.activities.all()


class CustomerEmailListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/emails/ — every
    organization-level Email for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Emails" filter on
    the Organization Details page's General tab."""

    serializer_class = EmailSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.emails.all()


class AccountEmailListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/emails/
    — every account-level Email for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Emails" filter on
    the standalone Account page."""

    serializer_class = EmailSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.emails.all()


class CustomerTaskListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/tasks/ — every
    organization-level Task for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Tasks" filter on
    the Organization Details page's General tab."""

    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.tasks.all()


class AccountTaskListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/tasks/
    — every account-level Task for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Tasks" filter on
    the standalone Account page."""

    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.tasks.all()


class CustomerNoteListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/notes/ — every
    organization-level Note for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Notes" filter on
    the Organization Details page's General tab."""

    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.notes.all()


class AccountNoteListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/notes/
    — every account-level Note for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Notes" filter on
    the standalone Account page."""

    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.notes.all()


class CustomerTicketListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/tickets/ — every
    organization-level Ticket for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Tickets" filter on
    the Organization Details page's General tab."""

    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.tickets.all()


class AccountTicketListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/tickets/
    — every account-level Ticket for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Tickets" filter on
    the standalone Account page."""

    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.tickets.all()


class CustomerCalendarEventListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/calendar-events/ — every
    organization-level CalendarEvent for one Customer, scoped to the
    caller's own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Calendar Events"
    filter on the Organization Details page's General tab."""

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
        return customer.calendar_events.all()


class AccountCalendarEventListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/calendar-events/
    — every account-level CalendarEvent for one Account, scoped to
    both its customer_id and the caller's own organisation. Same
    reasoning as AccountActivityListView. Powers ActivityFeed's
    "Calendar Events" filter on the standalone Account page."""

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )
        return account.calendar_events.all()


class CustomerContactListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/contacts/ — GET returns
    every Contact under this Customer, rolled up from both levels a
    Contact can exist at: organization-level (directly on this
    Customer) *and* account-level (on any of its Accounts) — see
    Contact's own docstring on why both shapes exist. Powers the
    Organization Details page's own Contacts tab, which shows both
    kinds together (ContactSerializer's `account_name` is how the
    frontend tells them apart — null for an organization-level row).

    POST always adds an organization-level Contact here — `customer`
    is taken from the URL, never client-supplied, same as
    AccountListCreateView's own `customer`. An account-level Contact is
    added via AccountContactListView below instead — including from
    the Organization Details page's own Add Contact form, which POSTs
    there directly once the caller picks one of this customer's
    accounts (see ContactFormModal.tsx's own account picker) — and via
    a customer_id the frontend picks from a dropdown rather than a URL
    param, the standalone /contacts/list page's "Add Contact".

    Scoped to the caller's own organisation. Same 404-not-empty-list
    convention as CustomerActivityListView."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )

    def get_queryset(self):
        customer = self.get_customer()
        return Contact.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountContactListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/contacts/
    — every account-level Contact for one Account (GET), or adds a new
    one to it (POST), scoped to both its customer_id and the caller's
    own organisation. Same reasoning as AccountActivityListView/
    AccountListCreateView. Powers the standalone Account page's own
    Contacts tab."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )

    def get_queryset(self):
        return self.get_account().contacts.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class ContactListView(generics.ListAPIView):
    """GET /api/v1/contacts/ — every Contact across every Customer/
    Account the caller's own organisation owns, organization-level and
    account-level alike. Powers the standalone Contacts page
    (react-ts-app's /contacts/list) — the one place a Contact is
    browsed independent of which Customer/Account it belongs to, so
    this is the one Contact view that isn't nested under
    /customers/<id>/... (mounted directly at /api/v1/contacts/ in the
    project's root urls.py instead).

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE —
    unlike every other List view in this file, which turns pagination
    off for what's normally a single entity's already-small nested
    list — since this one can span every contact the tenant has, same
    reasoning as CustomerListCreateView.

    `?search=` matches name/email/role (substring, case-insensitive),
    same convention as CustomerListCreateView's own search. `?company=
    <customer_id>` filters to one company, matching a contact directly
    on that Customer or on any of its Accounts."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` because `account__customers__organisation=` fans
        # out one row per matching linked Customer on the account — an
        # account-level Contact whose Account is linked to two+
        # Customers in this same organisation would otherwise appear
        # more than once.
        queryset = (
            Contact.objects.filter(
                Q(customer__organisation=organisation)
                | Q(account__customers__organisation=organisation)
            )
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(email__icontains=search) | Q(role__icontains=search)
            )

        company = self.request.query_params.get("company")
        if company:
            try:
                company_id = int(company)
            except ValueError:
                company_id = None
            if company_id is not None:
                queryset = queryset.filter(
                    Q(customer_id=company_id) | Q(account__customers__id=company_id)
                )

        return queryset


class ContactStatsView(views.APIView):
    """GET /api/v1/contacts/stats/ — aggregate rollups for the
    standalone Contacts page's MetricsPanel (Total/Active/Sentiment/
    Growth cards), scoped to the caller's own organisation, across
    every Contact (org-level and account-level alike) — same
    "spans everything, not just the current page" reasoning as
    CustomerStatsView.

    `growth_30d_pct` compares today's total against the total as of 30
    days ago (contacts whose `created_at` already predates the
    cutoff) — the only "growth" there's real data for; there's no
    historical daily-snapshot table to compare a true count-30-days-ago
    against anything richer. `None` (not 0) when there were no
    contacts yet 30 days ago, since a percentage change off a zero base
    is undefined, not zero."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        # `.distinct()` — same "an Account linked to two+ Customers in
        # this organisation would otherwise fan out" reason as
        # ContactListView's own.
        contacts = Contact.objects.filter(
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()

        total = contacts.count()
        active = contacts.filter(status=Contact.Status.ACTIVE).count()

        sentiment_counts = {
            sentiment: contacts.filter(sentiment=sentiment).count()
            for sentiment in Contact.Sentiment.values
        }
        sentiment_pct = {
            sentiment: (round(count / total * 100) if total else 0)
            for sentiment, count in sentiment_counts.items()
        }

        cutoff = timezone.now() - timedelta(days=30)
        total_30d_ago = contacts.filter(created_at__lte=cutoff).count()
        growth_30d_pct = (
            round((total - total_30d_ago) / total_30d_ago * 100, 1) if total_30d_ago else None
        )

        return Response(
            {
                "total": total,
                "active": active,
                "sentiment": sentiment_counts,
                "sentiment_pct": sentiment_pct,
                "growth_30d_pct": growth_30d_pct,
            }
        )


class ContactDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/contacts/<id>/ — a single Contact,
    scoped to the caller's own organisation (via either its `customer`
    or its `account`'s own `customers`, same reasoning as ContactListView's
    own queryset) regardless of whether it's an organization-level or
    account-level Contact. 404 (not 403) outside that scope.

    Deliberately flat (not nested under /customers/<id>/... like the
    two ListCreateAPIViews above) — Edit/Delete on any of the three
    Contacts UIs (standalone list, Organization Details, standalone
    Account page) only ever needs the Contact's own id, never its
    parent's, so there's no reason to make the caller thread a
    customer_id/account_id it may not even have on hand (the
    standalone /contacts/list page's own rows don't carry an
    account_id, only companies/account_name for display).

    PATCH can't move a Contact between parents — `customer`/`account`
    aren't in ContactSerializer's own `fields` list at all, so a PATCH
    body naming either is silently ignored rather than erroring."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return Contact.objects.filter(
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()


class CustomerOpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/opportunities/ — same
    shape as CustomerContactListView: GET rolls up every Opportunity
    under this Customer, both organisation-level (directly on it) and
    account-level (on any of its Accounts); POST always adds an
    organisation-level one, `customer` taken from the URL. An
    account-level Opportunity is added via AccountOpportunityListView
    below instead. Scoped to the caller's own organisation."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )

    def get_queryset(self):
        customer = self.get_customer()
        return Opportunity.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountOpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/opportunities/
    — every account-level Opportunity for one Account (GET), or adds a
    new one to it (POST); `account` taken from the URL. Same reasoning
    as AccountContactListView."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )

    def get_queryset(self):
        return self.get_account().opportunities.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class OpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/opportunities/ — every Opportunity across every
    Customer/Account the caller's own organisation owns, organisation-
    level and account-level alike. Powers the standalone Pipelines
    board's own "Opportunities" tab (react-ts-app's
    src/pages/pipelines/PipelinesPage.tsx) — the one place an
    Opportunity is browsed independent of which Customer/Account it
    belongs to, same reasoning as ContactListView.

    Unlike ContactListView, pagination is off here — a Kanban board
    needs every card in every column to render/drag-and-drop
    correctly, not one page of them; there's no reasonable way to
    paginate a board and keep every column complete.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Opportunity under that parent — the standalone board's own "Add
    Opportunity" picks a company (and optionally one of its accounts)
    the same way the standalone Contacts page's "Add Contact" does.
    Exactly one of the two must be given, same invariant as the model's
    own CheckConstraint."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return (
            Opportunity.objects.filter(
                Q(customer__organisation=organisation)
                | Q(account__customers__organisation=organisation)
            )
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        organisation = self.request.user.organisation
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — an Account
            # linked to two+ Customers in this organisation would
            # otherwise fan out into more than one row for the *same*
            # pk, which `.get()` (what get_object_or_404 calls) treats
            # as MultipleObjectsReturned rather than a single match.
            account = get_object_or_404(
                Account.objects.filter(customers__organisation=organisation).distinct(),
                pk=account_id,
            )
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(Customer, pk=customer_id, organisation=organisation)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class OpportunityDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/opportunities/<id>/ — a single
    Opportunity, scoped to the caller's own organisation, regardless of
    whether it's organisation-level or account-level. Flat, not nested
    — same reasoning as ContactDetailView. Powers both the board's
    drag-and-drop (PATCH `stage`) and its Edit/Delete card actions."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` — same fan-out reasoning as ContactDetailView's own.
        return Opportunity.objects.filter(
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()


class CustomerRiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/risks/ — same shape as
    CustomerOpportunityListView: GET rolls up every Risk under this
    Customer, both organisation-level (directly on it) and
    account-level (on any of its Accounts); POST always adds an
    organisation-level one, `customer` taken from the URL. An
    account-level Risk is added via AccountRiskListView below instead.
    Scoped to the caller's own organisation."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )

    def get_queryset(self):
        customer = self.get_customer()
        return Risk.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountRiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/risks/
    — every account-level Risk for one Account (GET), or adds a new one
    to it (POST); `account` taken from the URL. Same reasoning as
    AccountOpportunityListView."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customers=self.kwargs["customer_id"],
            customers__organisation=self.request.user.organisation,
        )

    def get_queryset(self):
        return self.get_account().risks.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class RiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/risks/ — every Risk across every Customer/
    Account the caller's own organisation owns, organisation-level and
    account-level alike. Powers the standalone Pipelines board's own
    "Risks" tab (react-ts-app's src/pages/pipelines/PipelinesPage.tsx)
    — the one place a Risk is browsed independent of which Customer/
    Account it belongs to, same reasoning as OpportunityListView.

    Unpaginated for the same reason as OpportunityListView — a Kanban
    board needs every card in every column to render/drag-and-drop
    correctly, not one page of them.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Risk under that parent. Exactly one of the two must be given,
    same invariant as the model's own CheckConstraint."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return (
            Risk.objects.filter(
                Q(customer__organisation=organisation)
                | Q(account__customers__organisation=organisation)
            )
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        organisation = self.request.user.organisation
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — same
            # reasoning as OpportunityListView.perform_create's own.
            account = get_object_or_404(
                Account.objects.filter(customers__organisation=organisation).distinct(),
                pk=account_id,
            )
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(Customer, pk=customer_id, organisation=organisation)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class RiskDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/risks/<id>/ — a single Risk, scoped to
    the caller's own organisation, regardless of whether it's
    organisation-level or account-level. Flat, not nested — same
    reasoning as OpportunityDetailView. Powers both the board's
    drag-and-drop (PATCH `stage`) and its Edit/Delete card actions."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        organisation = self.request.user.organisation
        # `.distinct()` — same fan-out reasoning as ContactDetailView's own.
        return Risk.objects.filter(
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()
