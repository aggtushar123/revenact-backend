"""A segment's rules as one `Q` over the kind's visible queryset.

`compile_rules` turns the stored JSON into a `Compiled`: the `Q`, the
annotations it reads, and which fields it names. `Compiled.apply` runs over a
queryset the caller has already scoped to what its viewer may open
(`evaluate.visible_records`), so a rule only ever reads records the viewer
may open:
- an organisation or account id narrows through `visible_customers` and
  `visible_accounts`;
- tickets are counted under the department rule, so the open-ticket count
  is the viewer's own;
- an AI attribute is read only on records the viewer may open.

The other figures are the record's own, organisation-wide: last touch, the
health score and band, the pulses, ARR. They are what the Organizations and
Accounts lists already show this viewer on the same rows, so a rule on them
reveals nothing the lists do not.

Nothing here is a copy:
- the health bands (`health_q`), NPS bands (`NPS_Q`), churn (`CHURNED`) and
  renewal windows (`renewing_q`, `account_renewing_q`) are the Organizations
  and Accounts lists' own;
- last touch is the rubric's `last_contact_annotation`, or the account's
  `last_account_contact_annotation`.

A stored rule naming a field, attribute or id that no longer resolves
matches nothing rather than failing. Rules are checked when written
(`rules.validate_rules`), and the data moves on after that.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import reduce
from operator import and_, or_

from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    F,
    FloatField,
    Func,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Round
from django.db.models.lookups import Exact

from services.accounts_portfolio.book import account_renewing_q
from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.contact import last_account_contact_annotation, last_contact_annotation
from services.customers.models import Account, Customer, Ticket
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_accounts, visible_customers
from services.fx_rates.conversion import rates_for
from services.organizations.book import CHURNED, NPS_Q, health_q, renewing_q

from . import registry
from .registry import DATE, DAYS, OWNER, RECORD, TEXT, UNASSIGNED
from .rules import leaves


def nothing():
    """A condition no record meets."""
    return Q(pk__in=[])


# The function name is fixed and its one expression is compiled by the ORM
# (the AIAttributeValue.value JSON column, never user input), so nothing
# reaches the SQL unescaped.
# nosemgrep: python.django.security.audit.extends-custom-expression.extends-custom-expression
class JsonbTypeof(Func):
    function = "jsonb_typeof"
    output_field = CharField()


def arr_expression(organisation, rates):
    """ARR as the workspace counts it, in the workspace's currency. The
    column is the one its global-attribute mapping names, times the
    admin-maintained rate, as one CASE over the workspace's few currencies:
    no join and no query per row. A currency with no rate is NULL, so it
    never meets an ARR condition and the tile counts it as unconverted. That
    is `convert_to_org_currency`'s rule, in SQL."""
    column = organisation.effective_global_attributes()["arr"]
    output = DecimalField(max_digits=24, decimal_places=6)
    whens = [When(currency=organisation.currency, then=Cast(F(column), output))]
    whens += [
        When(currency=currency, then=Cast(F(column) * Value(rate), output))
        for currency, rate in sorted(rates.items())
        if currency != organisation.currency
    ]
    return Case(*whens, default=None, output_field=output)


def seat_use_expression():
    """`Customer.seat_utilization_percentage` in SQL: active over contracted
    seats, as a percentage rounded to 2 decimals, the figure the page shows
    (so a rule at the boundary agrees with it). NULL with no contracted seats
    or no active count."""
    output = DecimalField(max_digits=12, decimal_places=2)
    exact = DecimalField(max_digits=24, decimal_places=10)
    return Case(
        When(
            total_contracted_seats__gt=0,
            total_active_seats__isnull=False,
            then=Round(
                Cast(F("total_active_seats"), exact)
                * Value(100)
                / Cast(F("total_contracted_seats"), exact),
                2,
            ),
        ),
        default=None,
        output_field=output,
    )


def open_tickets_expression(user, kind):
    """Unresolved tickets filed on the record itself (the rubric's scope)
    that `user` may read under the department rule."""
    parent = "account" if kind == "account" else "customer"
    # SOC2:AUTH-02 counted under the reader's own department rule
    tickets = visible_tickets(user, Ticket.objects.filter(**{parent: OuterRef("pk")}))
    counted = (
        tickets.exclude(status__in=Ticket.RESOLVED_STATUSES)
        .order_by()
        .values(parent)
        .annotate(n=Count("id"))
        .values("n")[:1]
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def latest_value(attribute, kind):
    """The newest answer to `attribute` on the outer record: the row the
    attribute panel shows (newest `computed_at`, then id)."""
    parent = "account" if kind == "account" else "customer"
    rows = (
        AIAttributeValue.objects.filter(attribute=attribute, **{parent: OuterRef("pk")})
        .order_by("-computed_at", "-id")
        .values("value")[:1]
    )
    return Subquery(rows, output_field=JSONField())


def latest_number(attribute, kind):
    """`latest_value` as a number, or NULL when the newest answer is not a
    JSON number. An attribute whose type changed keeps its old answers, and
    casting one of them must not fail the page. One subquery: the CASE on
    `jsonb_typeof` is evaluated inside it, on the newest row."""
    parent = "account" if kind == "account" else "customer"
    rows = (
        AIAttributeValue.objects.filter(attribute=attribute, **{parent: OuterRef("pk")})
        .order_by("-computed_at", "-id")
        .annotate(
            _number=Case(
                When(
                    Exact(JsonbTypeof(F("value")), Value("number")),
                    then=Cast(F("value"), FloatField()),
                ),
                default=None,
                output_field=FloatField(),
            )
        )
        .values("_number")[:1]
    )
    return Subquery(rows, output_field=FloatField())


@dataclass
class Compiled:
    kind: str
    q: Q
    annotations: dict
    #: Every field key the rules name, `parent.` prefixes kept.
    named: frozenset
    #: Each top-level condition as `(field keys, Q)`, for a change's reason.
    conditions: tuple
    #: True when a rule names `churned`, or a lifecycle stage condition asks
    #: for `churn` with `is`/`in`: the Organizations list lifts its churned
    #: default for `churn` among the lifecycle filters, and so does a segment
    #: (rulings S4, S4a). `is_not churn` keeps the default.
    names_churned: bool = False

    def annotate(self, queryset):
        return queryset.annotate(**self.annotations) if self.annotations else queryset

    def apply(self, queryset):
        """The records of `queryset` the rules match. Churned and archived
        organisations are left out unless a rule names them, as on the
        Organizations list."""
        queryset = self.annotate(queryset).filter(self.q)
        if self.kind == "customer":
            if not self.names_churned:
                queryset = queryset.exclude(CHURNED)
            if "archived" not in self.named:
                queryset = queryset.filter(is_archived=False)
        return queryset


def _names_churned(rules):
    churn = Customer.LifecycleStage.CHURN
    for condition in leaves(rules):
        field, op, value = condition.get("field"), condition.get("op"), condition.get("value")
        if field == "churned":
            return True
        if field != "lifecycle_stage":
            continue
        if op == "is" and value == churn:
            return True
        if op == "in" and isinstance(value, list) and churn in value:
            return True
    return False


def _combine(match, parts):
    if not parts:
        return nothing()
    return reduce(or_ if match == "any" else and_, parts)


def _choice(op, value, q_for):
    if op == "is":
        return q_for(value)
    if op == "is_not":
        return ~q_for(value)
    if op == "in":
        return _combine("any", [q_for(part) for part in value])
    return nothing()


def _owner(value):
    if value == UNASSIGNED:
        return Q(owner__isnull=True)
    if value is None:
        return nothing()
    return Q(owner_id=value)


def _ids(column, op, value):
    ids = [part for part in (value if op == "in" else [value]) if part is not None]
    hit = Q(**{f"{column}__in": ids})
    return ~hit if op == "is_not" else hit


class Compiler:
    def __init__(self, kind, *, user, today, cache=None):
        self.kind = kind
        self.user = user
        self.today = today
        #: Shared with `parent.` sub-compilers: one FX read, one attribute read.
        self.cache = cache if cache is not None else {}
        self.annotations = {}

    @property
    def organisation(self):
        return self.user.organisation

    @property
    def rates(self):
        if "rates" not in self.cache:
            self.cache["rates"] = rates_for(self.organisation)
        return self.cache["rates"]

    @property
    def attributes(self):
        return self.cache.get("attributes", {})

    def annotated(self, queryset):
        return queryset.annotate(**self.annotations) if self.annotations else queryset

    def compile(self, rules):
        rules = rules or {}
        if "attributes" not in self.cache:
            keys = [condition.get("field", "") for condition in leaves(rules)]
            self.cache["attributes"] = registry.attributes_for(self.user.organisation_id, keys)
        conditions = []
        for condition in rules.get("conditions") or []:
            if "group" in condition:
                group = condition["group"]
                members = group.get("conditions") or []
                q = _combine(group.get("match"), [self.leaf_q(leaf) for leaf in members])
                keys = tuple(leaf.get("field", "") for leaf in members)
            else:
                q = self.leaf_q(condition)
                keys = (condition.get("field", ""),)
            conditions.append((keys, q))
        return Compiled(
            kind=self.kind,
            q=_combine(rules.get("match"), [q for _keys, q in conditions]),
            annotations=dict(self.annotations),
            named=frozenset(key for keys, _q in conditions for key in keys),
            conditions=tuple(conditions),
            names_churned=_names_churned(rules),
        )

    def leaf_q(self, condition):
        key, op, value = condition.get("field", ""), condition.get("op"), condition.get("value")
        if self.kind == "contact" and key.startswith(registry.PARENT_PREFIX):
            return self._parent(key.removeprefix(registry.PARENT_PREFIX), op, value)
        field = registry.resolve(self.kind, key, self.attributes)
        if field is None or op not in field.operators:
            return nothing()
        return self._field_q(field, op, value)

    def _field_q(self, field, op, value):
        key = field.key
        if key == "health_category":
            return _choice(op, value, health_q)
        if key == "nps_band":
            return _choice(op, value, NPS_Q.__getitem__)
        if key == "churned":
            return CHURNED if value else ~CHURNED
        if key == "renewal_date" and op == "within_next":
            # Overdue counts as within: the lists' own renewal rule.
            renewing = renewing_q if self.kind == "customer" else account_renewing_q
            return renewing(value, today=self.today)
        if field.type == OWNER:
            return _choice(op, value, _owner)
        if key == "organisation":
            return self._organisation(op, value)
        if key == "account":
            return self._account(op, value)
        if field.type == RECORD:
            return _ids(field.column, op, value)
        json = key.startswith(registry.ATTRIBUTE_PREFIX)
        return self._typed(field.type, self._column(field), op, value, json=json)

    def _annotate(self, name, build):
        if name not in self.annotations:
            self.annotations[name] = build()
        return name

    def _column(self, field):
        key = field.key
        if key == "arr" and self.kind == "customer":
            return self._annotate("_seg_arr", lambda: arr_expression(self.organisation, self.rates))
        if key == "seat_use":
            return self._annotate("_seg_seat_use", seat_use_expression)
        if key == "open_tickets":
            return self._annotate(
                "_seg_open_tickets", lambda: open_tickets_expression(self.user, self.kind)
            )
        if key == "last_touch":
            touch = (
                last_contact_annotation
                if self.kind == "customer"
                else last_account_contact_annotation
            )
            return self._annotate("_seg_touch", touch)
        if key.startswith(registry.ATTRIBUTE_PREFIX):
            attribute = self.attributes[key.removeprefix(registry.ATTRIBUTE_PREFIX)]
            if attribute.value_type == AIAttribute.ValueType.NUMBER:
                return self._annotate(
                    f"_seg_attr_number_{attribute.pk}",
                    lambda: latest_number(attribute, self.kind),
                )
            return self._annotate(
                f"_seg_attr_{attribute.pk}", lambda: latest_value(attribute, self.kind)
            )
        return field.column

    def _typed(self, type_, column, op, value, *, json=False):
        if op == "is_empty":
            empty = Q(**{f"{column}__isnull": True})
            return empty | Q(**{column: ""}) if type_ == TEXT else empty
        if op == "is_not_empty":
            present = Q(**{f"{column}__isnull": False})
            return present & ~Q(**{column: ""}) if type_ == TEXT else present
        if type_ == DAYS:
            return self._days(column, op, value)
        if type_ == DATE and op in ("gt", "lt"):
            value = date.fromisoformat(value)
        if type_ == DATE and op == "between":
            value = [date.fromisoformat(part) for part in value]
        if op == "gt":
            return Q(**{f"{column}__gt": value})
        if op == "lt":
            return Q(**{f"{column}__lt": value})
        if op == "between":
            low, high = value
            return Q(**{f"{column}__gte": low, f"{column}__lte": high})
        if op == "within_next":
            end = self.today + timedelta(days=value)
            return Q(**{f"{column}__gte": self.today, f"{column}__lte": end})
        if op == "within_last":
            start = self.today - timedelta(days=value)
            return Q(**{f"{column}__gte": start, f"{column}__lte": self.today})
        if op == "is":
            return Q(**{column: value})
        if op == "is_not":
            # "Is not X" includes "has no value", as a person reads it.
            return ~Q(**{column: value}) | Q(**{f"{column}__isnull": True})
        if op == "in":
            if json:
                return _combine("any", [Q(**{column: part}) for part in value])
            return Q(**{f"{column}__in": list(value)})
        return nothing()

    def _days(self, column, op, value):
        """Days since `column`. Never touched is more days than any number."""

        def ago(days):
            return self.today - timedelta(days=days)

        if op == "gt":
            return Q(**{f"{column}__lt": ago(value)}) | Q(**{f"{column}__isnull": True})
        if op == "lt":
            return Q(**{f"{column}__gt": ago(value)})
        if op == "between":
            low, high = value
            return Q(**{f"{column}__gte": ago(high), f"{column}__lte": ago(low)})
        return nothing()

    def _openable(self, record, op, value):
        ids = [part for part in (value if op == "in" else [value]) if part is not None]
        # SOC2:AUTH-02 an id the viewer cannot open names nothing in their book
        records = (
            visible_customers(self.user) if record == "customer" else visible_accounts(self.user)
        )
        return records.filter(pk__in=ids).values("pk")

    def _organisation(self, op, value):
        """An account linked to the organisation; a contact on it or on one of
        its accounts."""
        openable = self._openable("customer", op, value)
        if self.kind == "account":
            hit = Q(customers__in=openable)
        else:
            hit = Q(customer__in=openable) | Q(account__customers__in=openable)
        return ~hit if op == "is_not" else hit

    def _account(self, op, value):
        hit = Q(account__in=self._openable("account", op, value))
        return ~hit if op == "is_not" else hit

    def _parent(self, key, op, value):
        """A contacts rule on the contact's own organisation or account. The
        condition is compiled for each parent kind that has the field, over
        that kind's records in the workspace, and a contact matches when its
        own parent does. A contact is visible only through a parent its viewer
        may open, so this reads nothing the viewer could not. That claim rests
        on the `contact_belongs_to_exactly_one_parent` check constraint on
        Contact: a contact has its organisation or its account, never both, so
        the parent a condition reads is always the one its visibility came
        through."""
        if key in registry.PARENT_EXCLUDED:
            return nothing()
        organisation_id = self.user.organisation_id
        parents = (
            ("customer", "customer__in", Customer.objects.filter(organisation_id=organisation_id)),
            (
                "account",
                "account__in",
                Account.objects.filter(customers__organisation_id=organisation_id),
            ),
        )
        sides = []
        for parent_kind, relation, records in parents:
            if registry.resolve(parent_kind, key, self.attributes) is None:
                continue
            sub = Compiler(parent_kind, user=self.user, today=self.today, cache=self.cache)
            condition = sub.leaf_q({"field": key, "op": op, "value": value})
            sides.append(Q(**{relation: sub.annotated(records).filter(condition).values("pk")}))
        return _combine("any", sides)


def compile_rules(rules, kind, *, user, today):
    return Compiler(kind, user=user, today=today).compile(rules)
