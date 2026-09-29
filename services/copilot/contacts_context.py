"""Where on Contacts a question was asked, as the client sends it.

The list (`view: "list"`) sends the page's URL filters: `q, customer,
account, sentiment, role`. One person's profile (`view: "person"`) sends the
contact's id and, from "Why this sentiment?", `focus: "sentiment"`. Nothing
else is read: never a name, never figures, and never the client's `label`.
Filters go through the list's own parser (`contact_list.parse_contact_filters`),
so a value the list would ignore is dropped here too. They are stored in the
page's own URL form, ready to restore it.

An organisation, account or person the asker cannot open is a 400 that reads
the same whether the id exists or not. The chip's label is built here, from
rows the asker may open.
"""

from rest_framework import serializers

from services.customers.contact_list import ContactFilters, parse_contact_filters
from services.customers.models import Account, Contact, Customer
from services.customers.scoping import visible_accounts, visible_children_q, visible_customers

SURFACE = "contacts"
LIST = "list"
PERSON = "person"
FOCUS_SENTIMENT = "sentiment"
SEPARATOR = " · "

NOT_A_PERSON = "Not a person you can open."
NOT_OPEN_ORGANISATION = "Not an organisation you can open."
NOT_OPEN_ACCOUNT = "Not an account you can open."

#: The page's URL keys, in the order they are stored and named.
FILTER_KEYS = ("q", "customer", "account", "sentiment", "role")
#: A search longer than this is not a search; it is dropped.
MAX_SEARCH_LENGTH = 100


def _text(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def canonical(filters: ContactFilters) -> dict:
    """ContactFilters back as the page's URL parameters: only what is set."""
    stored = {}
    if filters.search:
        stored["q"] = filters.search
    if filters.customer is not None:
        stored["customer"] = str(filters.customer)
    if filters.account is not None:
        stored["account"] = str(filters.account)
    if filters.sentiment:
        stored["sentiment"] = filters.sentiment
    if filters.role:
        stored["role"] = filters.role
    return stored


def filters_of(stored: dict) -> ContactFilters:
    """Stored (canonical) filters as ContactFilters."""
    query = {("search" if key == "q" else key): value for key, value in stored.items()}
    return parse_contact_filters(query)


def visible_contact(user, pk):
    """The contact if `user` may open it, else None."""
    # SOC2:AUTH-02 a contact follows its organisation's or account's visibility
    return (
        Contact.objects.filter(visible_children_q(user))
        .select_related("customer", "account")
        .prefetch_related("account__customers")
        .distinct()
        .filter(pk=pk)
        .first()
    )


def organisation_of(contact, visible_customer_ids):
    """The organisation a person sits under for this viewer: their own, or
    the first of their account's linked organisations the viewer may open
    (by pk, as ContactSerializer.get_organisation reads it); None if none."""
    if contact.customer_id:
        return contact.customer
    linked = sorted(contact.account.customers.all(), key=lambda customer: customer.pk)
    return next((c for c in linked if c.pk in visible_customer_ids), None)


def place_label(contact, visible_customer_ids):
    organisation = organisation_of(contact, visible_customer_ids)
    parts = [organisation.name if organisation else "No organisation"]
    if contact.account_id:
        parts.append(contact.account.name)
    return " › ".join(parts)


def list_label(user, filters: ContactFilters):
    parts = ["Contacts"]
    if filters.customer is not None:
        place = Customer.objects.get(pk=filters.customer).name
        if filters.account is not None:
            place += " › " + Account.objects.get(pk=filters.account).name
        parts.append(place)
    elif filters.account is not None:
        parts.append(Account.objects.get(pk=filters.account).name)
    if filters.sentiment:
        parts.append(Contact.Sentiment(filters.sentiment).label)
    if filters.role:
        parts.append(Contact.Role(filters.role).label)
    if filters.search:
        parts.append(f'"{filters.search}"')
    return SEPARATOR.join(parts)


def clean_filters(raw) -> ContactFilters:
    """The client's filters through the list's own parser. Unknown keys and
    unusable values are dropped, never rejected."""
    if not isinstance(raw, dict):
        return ContactFilters()
    query = {}
    for key in FILTER_KEYS:
        value = _text(raw.get(key))
        if value is None:
            continue
        if key == "q" and len(value.strip()) > MAX_SEARCH_LENGTH:
            continue
        query["search" if key == "q" else key] = value
    return parse_contact_filters(query)


class ContactsContextSerializer(serializers.Serializer):
    """Validates a Contacts send's `context`. Needs `context={"user": user}`."""

    surface = serializers.ChoiceField(choices=[SURFACE])
    view = serializers.ChoiceField(choices=[LIST, PERSON])
    filters = serializers.DictField(required=False, default=dict)
    contact = serializers.IntegerField(min_value=1, required=False)
    focus = serializers.ChoiceField(
        choices=[FOCUS_SENTIMENT], allow_null=True, required=False, default=None
    )

    def validate(self, data):
        user = self.context["user"]
        if data["view"] == PERSON:
            return self._person(user, data)
        return self._list(user, data)

    def _list(self, user, data):
        filters = clean_filters(data.get("filters"))
        # SOC2:AUTH-02 a named organisation or account must be one the asker may
        # open; the 400 reads the same whether it exists or not
        if (
            filters.customer is not None
            and not visible_customers(user).filter(pk=filters.customer).exists()
        ):
            raise serializers.ValidationError({"filters": {"customer": [NOT_OPEN_ORGANISATION]}})
        if (
            filters.account is not None
            and not visible_accounts(user).filter(pk=filters.account).exists()
        ):
            raise serializers.ValidationError({"filters": {"account": [NOT_OPEN_ACCOUNT]}})
        return {
            "surface": SURFACE,
            "view": LIST,
            "filters": canonical(filters),
            "label": list_label(user, filters),
        }

    def _person(self, user, data):
        pk = data.get("contact")
        contact = visible_contact(user, pk) if pk is not None else None
        # SOC2:AUTH-02 the person must be one the asker may open; the 400 reads
        # the same whether they exist or not
        if contact is None:
            raise serializers.ValidationError({"contact": [NOT_A_PERSON]})
        visible_ids = set(visible_customers(user).values_list("pk", flat=True))
        return {
            "surface": SURFACE,
            "view": PERSON,
            "contact": contact.pk,
            "label": f"{contact.name}{SEPARATOR}{place_label(contact, visible_ids)}",
            "focus": data["focus"],
        }
