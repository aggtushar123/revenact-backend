"""Where on one organisation's page a question was asked (`view: "detail"`).

The client sends the organisation's id, the account chip's id if one is
selected, and at most one story item it is asking about — never a name or any
text. Both ids are checked with the story's own scope
(`services.organizations.story.scope.resolve_scope`): the organisation must be
in `visible_customers(asker)`, and the account must be one of its accounts in
`visible_accounts(asker)`. Either failing is a 400 that reads the same whether
the id exists or not. The focus item is read again with the story's own
source rules (`find_item`); an item the asker may not read is dropped without
saying so, as a list focus is. The chip's label, "Pizza Hut" or
"Pizza Hut · EMEA", is built here from those rows, never taken from the
client.
"""

from django.http import Http404
from django.utils import timezone
from rest_framework import serializers

from services.organizations.story.build import HEALTH, matches_account
from services.organizations.story.health import health_entries
from services.organizations.story.items import render
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES, horizon_for

DETAIL = "detail"
#: The story kinds "Ask about this" may name: every record source, and health.
FOCUS_KINDS = (*sorted(SOURCES), HEALTH)
SEPARATOR = " · "

NOT_OPEN = "Not an organisation you can open."
NOT_AN_ACCOUNT = "Not an account of this organisation you can open."


def detail_label(scope, account):
    """The chip and the history tag: the organisation, then the account."""
    if account is None:
        return scope.customer.name
    return f"{scope.customer.name}{SEPARATOR}{scope.accounts[account]}"


def find_item(user, scope, kind, ident, *, account, today):
    """The story item `(kind, ident)` exactly as the story renders it for
    `user`, or None when it is not one they may read on this page: filed on
    another organisation or another account, outside the account chip, dated
    after today, or refused by its own record rule."""
    horizon = horizon_for(today)
    if kind == HEALTH:
        for _key, item in health_entries(scope, horizon=horizon):
            account_id = item["account"]["id"] if item["account"] else None
            if item["id"] == ident and matches_account(account_id, account):
                return item
        return None
    source = SOURCES[kind]
    row = (
        # SOC2:AUTH-02 the story's own base: the organisation's scope, then the
        # record's own rule
        source.base(user, scope, horizon=horizon)
        .filter(scope.parent_q(account), pk=ident)
        .select_related(*source.related)
        .first()
    )
    return None if row is None else render(kind, row, scope)


class DetailFocusSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=FOCUS_KINDS)
    id = serializers.IntegerField(min_value=1)


class OrganizationDetailContextSerializer(serializers.Serializer):
    """Validates a send from one organisation's page. Needs
    `context={"user": user}`. Anything else the client sends — a `label`
    included — is ignored."""

    surface = serializers.ChoiceField(choices=["organizations"])
    view = serializers.ChoiceField(choices=[DETAIL])
    organization = serializers.IntegerField(min_value=1)
    account = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    focus = DetailFocusSerializer(allow_null=True, required=False, default=None)

    def validate(self, data):
        user = self.context["user"]
        try:
            # SOC2:AUTH-02 the organisation must be one the asker may open
            scope = resolve_scope(user, data["organization"])
        except Http404:
            raise serializers.ValidationError({"organization": [NOT_OPEN]}) from None
        account = data["account"]
        # SOC2:AUTH-02 and the account one of its accounts the asker may open
        if account is not None and account not in scope.accounts:
            raise serializers.ValidationError({"account": [NOT_AN_ACCOUNT]})
        return {
            "surface": "organizations",
            "view": DETAIL,
            "organization": scope.customer.pk,
            "account": account,
            "label": detail_label(scope, account),
            "focus": self._focus(user, scope, data["focus"], account),
        }

    def _focus(self, user, scope, focus, account):
        if focus is None:
            return None
        kind, ident = focus["kind"], focus["id"]
        item = find_item(user, scope, kind, ident, account=account, today=timezone.localdate())
        # SOC2:AUTH-02 an item the asker may not read is dropped without saying so
        return None if item is None else {"kind": kind, "id": ident}
