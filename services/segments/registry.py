"""Which fields a segment rule may name, per kind, and which operators each
takes.

A fixed list, because a saved rule is JSON and a field name in it is a
claim, not a permission (the scenario engine's `CONDITION_ATTRIBUTES` rule).
An AI attribute is named `attr:<api_name>`, as in a scenario condition. A
contacts rule names a field of the contact's own organisation or account as
`parent.<key>`. How each field compiles lives in `compiler.py`; this module
only says what exists and what it accepts.
"""

from dataclasses import dataclass

from services.attributes.models import AIAttribute
from services.customers.models import Contact, Customer
from services.organizations.params import NPS_BANDS

NUMBER = "number"
PERCENT = "percent"
#: Days since a date: "never" is more days than any number.
DAYS = "days"
DATE = "date"
CHOICE = "choice"
TEXT = "text"
BOOLEAN = "boolean"
OWNER = "owner"
#: An organisation, account or product id.
RECORD = "record"

OPERATORS = {
    NUMBER: ("gt", "lt", "between", "is_empty", "is_not_empty"),
    PERCENT: ("gt", "lt", "between", "is_empty", "is_not_empty"),
    DAYS: ("gt", "lt", "between", "is_empty"),
    DATE: ("within_next", "within_last", "gt", "lt", "between", "is_empty", "is_not_empty"),
    CHOICE: ("is", "is_not", "in"),
    TEXT: ("is", "is_not", "in", "is_empty", "is_not_empty"),
    BOOLEAN: ("is",),
    OWNER: ("is", "is_not", "in"),
    RECORD: ("is", "is_not", "in"),
}

ATTRIBUTE_PREFIX = "attr:"
PARENT_PREFIX = "parent."
UNASSIGNED = "unassigned"
KIND_NOUNS = {"customer": "organisations", "account": "accounts", "contact": "contacts"}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    #: The ORM path a plain comparison reads. Empty for the fields
    #: `compiler.py` builds itself (health band, NPS band, ARR, seat use,
    #: tickets, touch, churn, the organisation and account pickers).
    column: str = ""
    choices: tuple = ()
    #: For OWNER and RECORD: what an id in the value names — "user",
    #: "customer", "account" or "product".
    record: str = ""
    #: True for an AI attribute: "not answered yet" is a value worth asking
    #: about, so its type also takes `is_empty` and `is_not_empty`.
    optional: bool = False

    @property
    def operators(self):
        operators = OPERATORS[self.type]
        if self.optional:
            operators += tuple(op for op in ("is_empty", "is_not_empty") if op not in operators)
        return operators


def _fields(*fields):
    return {field.key: field for field in fields}


#: What an organisation and an account both have.
_SHARED = (
    Field(
        "lifecycle_stage",
        "Lifecycle stage",
        CHOICE,
        "lifecycle_stage",
        tuple(Customer.LifecycleStage.values),
    ),
    Field("health_score", "Health score", NUMBER, "health_score"),
    Field("health_category", "Health", CHOICE, choices=tuple(Customer.HealthCategory.values)),
    Field("csat_score", "CSAT %", PERCENT, "csat_score"),
    Field("nps_score", "NPS", NUMBER, "nps_score"),
    Field("nps_band", "NPS band", CHOICE, choices=NPS_BANDS),
    # An organisation's ARR is built in SQL (the mapping, converted); an
    # account's is its own `arr` column, already in the workspace currency.
    Field("arr", "ARR", NUMBER, "arr"),
    Field("renewal_date", "Renewal date", DATE, "renewal_date"),
    Field("owner", "Owner", OWNER, "owner", record="user"),
    Field("open_tickets", "Open tickets", NUMBER),
    Field("last_touch", "Days since last touch", DAYS),
    Field("ai_pulse", "AI pulse", NUMBER, "ai_pulse_value"),
    Field("csm_pulse", "CSM pulse", NUMBER, "csm_pulse_score"),
    Field("created", "Created date", DATE, "created_at__date"),
)

CUSTOMER_FIELDS = _fields(
    *_SHARED,
    Field("ces_percentage", "CES %", PERCENT, "ces_percentage"),
    Field("product", "Product", RECORD, "primary_product_id", record="product"),
    # Rounded to 2 decimals by the compiler, as the page shows it (ruling S20).
    Field("seat_use", "Seat use %", PERCENT),
    # The compiler also counts a lifecycle condition naming "churn" as naming
    # this, for the churned default (ruling S4).
    Field("churned", "Churned", BOOLEAN),
    Field("archived", "Archived", BOOLEAN, "is_archived"),
)
ACCOUNT_FIELDS = _fields(
    *_SHARED,
    Field("organisation", "Organisation", RECORD, record="customer"),
)
CONTACT_FIELDS = _fields(
    Field("role", "Role", CHOICE, "role", tuple(Contact.Role.values)),
    Field("sentiment", "Sentiment", CHOICE, "sentiment", tuple(Contact.Sentiment.values)),
    Field("status", "Status", CHOICE, "status", tuple(Contact.Status.values)),
    Field("language", "Language", TEXT, "language"),
    Field("last_contacted", "Days since last contacted", DAYS, "last_contacted_at__date"),
    Field("organisation", "Organisation", RECORD, record="customer"),
    Field("account", "Account", RECORD, record="account"),
)
FIELDS = {"customer": CUSTOMER_FIELDS, "account": ACCOUNT_FIELDS, "contact": CONTACT_FIELDS}

#: Where a contact's `parent.` field is read, in this order.
PARENT_KINDS = ("customer", "account")
#: Parent fields a contacts rule may not name: the contact has its own.
PARENT_EXCLUDED = frozenset({"organisation"})

_ATTRIBUTE_TYPES = {
    AIAttribute.ValueType.NUMBER: NUMBER,
    AIAttribute.ValueType.BOOLEAN: BOOLEAN,
    AIAttribute.ValueType.PICKLIST: CHOICE,
    AIAttribute.ValueType.TEXT: TEXT,
}


def attribute_field(attribute):
    return Field(
        key=f"{ATTRIBUTE_PREFIX}{attribute.api_name}",
        label=attribute.name,
        type=_ATTRIBUTE_TYPES[attribute.value_type],
        choices=tuple(attribute.picklist_options or ()),
        optional=True,
    )


def applies(attribute, kind):
    return attribute.applies_to_account if kind == "account" else attribute.applies_to_customer


def attribute_name(key):
    """`attr:tier` and `parent.attr:tier` → "tier"; any other key → None."""
    key = key.removeprefix(PARENT_PREFIX)
    return key.removeprefix(ATTRIBUTE_PREFIX) if key.startswith(ATTRIBUTE_PREFIX) else None


def attributes_for(organisation_id, keys):
    """The workspace's AI attributes the keys name, by api name: one query,
    none when no key names one."""
    names = {name for name in map(attribute_name, keys) if name}
    if not names:
        return {}
    rows = AIAttribute.objects.filter(organisation_id=organisation_id, api_name__in=names)
    return {attribute.api_name: attribute for attribute in rows}


def resolve(kind, key, attributes):
    """The Field `key` names for `kind`, or None. `attributes` is
    `attributes_for`'s answer; an attribute that does not apply to the kind
    reads as unknown."""
    if key.startswith(ATTRIBUTE_PREFIX):
        attribute = attributes.get(key.removeprefix(ATTRIBUTE_PREFIX))
        if attribute is None or not applies(attribute, kind):
            return None
        return attribute_field(attribute)
    return FIELDS[kind].get(key)


def resolve_parent(key, attributes):
    """A contacts rule's `parent.<key>`: the field as an organisation reads
    it, else as an account does."""
    if key in PARENT_EXCLUDED:
        return None
    for parent_kind in PARENT_KINDS:
        field = resolve(parent_kind, key, attributes)
        if field is not None:
            return field
    return None


def resolve_any(kind, key, attributes):
    if kind == "contact" and key.startswith(PARENT_PREFIX):
        return resolve_parent(key.removeprefix(PARENT_PREFIX), attributes)
    return resolve(kind, key, attributes)


def static_field(kind, key):
    """`resolve_any` without the workspace's attributes, which never hold
    ids: enough to find the owner and record fields in stored rules."""
    return resolve_any(kind, key, {})
