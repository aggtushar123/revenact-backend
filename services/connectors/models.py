from django.db import models

from services.accounts.models import Organisation, User
from services.mail import crypto


class Connector(models.Model):
    """One external system an organisation has connected — the Zendesk
    its support team lives in, the Jira its engineers file bugs into.

    Backs the "Tickets By Origin" chart on the Ticket Overview
    dashboard, and exists so a ticket can say where it came from
    (`customers.Ticket.connector`) rather than the dashboard grouping
    on free text.

    # Scope: empty means everywhere

    A connector with no linked `customers` and no linked `accounts`
    covers the **whole organisation**. Linking any narrows it to
    exactly those — so Apple can be on Zendesk while Kraft Heinz is on
    Jira, and an org-wide Slack connector needs no links at all.

    Empty-means-everything rather than requiring every customer to be
    listed: an admin shouldn't have to enumerate fifteen companies to
    say "all of them", and a new customer added tomorrow would silently
    fall outside an enumerated connector without anyone noticing. It's
    also the convention `Account.customers` already uses for "no
    restriction". The cost is that "covers nothing" is inexpressible —
    but that's what `is_enabled` is for, and a connector covering
    nothing would have no reason to exist.

    # Ticket sources and departments

    A ticket provider (Zendesk, Jira, Slack, Freshdesk, or any other
    system through the inbound webhook) is a live connection: an admin
    connects it with the organisation's own credentials (encrypted at
    rest, see `set_credentials`), the scheduler pulls new and changed
    tickets every few minutes (`sync.py`), and each ticket is filed on
    the customer or account its requester belongs to.

    Every ticket connector belongs to a **department** (`User.Function`):
    the Zendesk the support team lives in is Customer Success's, the Jira
    project is Engineering's. Tickets synced through it are stamped with
    that department, and only that department's people — and Leadership —
    can read them (services/customers/personal.py:visible_tickets). A
    connector with no department is everyone's."""

    class Provider(models.TextChoices):
        """The systems the product knows how to attribute a record to.

        Matches the vendor list the Integrations page already
        advertises (react-ts-app's pages/integrations/Integrations.tsx)
        so the two can't drift into naming the same tool differently.
        A closed set rather than free text, same reasoning as
        `Headline.DataSource`: an open field would let a connector
        claim to be a system this product has never heard of, and the
        origin chart would group on typos."""

        ZENDESK = "zendesk", "Zendesk"
        JIRA = "jira", "Jira Software"
        FRESHDESK = "freshdesk", "Freshdesk"
        WEBHOOK = "webhook", "Any other system (webhook)"
        INTERCOM = "intercom", "Intercom"
        SALESFORCE = "salesforce", "Salesforce"
        HUBSPOT = "hubspot", "HubSpot"
        SLACK = "slack", "Slack"
        GMAIL = "gmail", "Gmail"
        MS_TEAMS = "ms_teams", "Microsoft Teams"
        # Added when Call arrived: a recorded call has to be able to say which
        # meeting platform it came from, and Zoom was already advertised on the
        # Integrations page this list mirrors. tl;dv and Gong — what the
        # CallSense mock names — are deliberately absent, because that page
        # doesn't offer them and this list follows it rather than leading it.
        ZOOM = "zoom", "Zoom"
        GITHUB = "github", "GitHub"
        FIGMA = "figma", "Figma"

    class Status(models.TextChoices):
        NOT_CONNECTED = "not_connected", "Not connected"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Needs attention"

    organisation = models.ForeignKey(
        Organisation, related_name="connectors", on_delete=models.CASCADE
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    department = models.CharField(
        max_length=16,
        choices=User.Function.choices,
        blank=True,
        default="",
        help_text="Whose tickets these are. Blank means the whole company may read them.",
    )
    #: Fernet-encrypted JSON: an API token, a bot token, or the webhook's
    #: shared secret. Restricted; never serialised.
    credentials = models.TextField(blank=True, default="")
    #: Non-secret setup: a Zendesk subdomain, a Jira site and project, a
    #: Slack channel.
    config = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NOT_CONNECTED)
    error = models.CharField(max_length=255, blank=True, default="")
    sync_cursor = models.CharField(max_length=512, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text='What the last pass did, e.g. "3 new, 2 updated, 1 without a matching account".',
    )
    name = models.CharField(
        max_length=100,
        help_text='Distinguishes two of the same provider, e.g. "Zendesk (EU)" '
        'alongside "Zendesk (US)".',
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="A disabled connector keeps its tickets and its history — it "
        "just stops being offered as somewhere new records can come from.",
    )
    customers = models.ManyToManyField(
        "customers.Customer",
        related_name="connectors",
        blank=True,
        help_text="Leave empty to cover every customer in the organisation.",
    )
    accounts = models.ManyToManyField(
        "customers.Account",
        related_name="connectors",
        blank=True,
        help_text="Leave empty to cover every account in the organisation.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "provider", "name"],
                name="unique_connector_name_per_provider_per_organisation",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_provider_display()})"

    # Credentials never leave this pair of methods in clear.
    def set_credentials(self, data: dict):
        import json

        # SOC2:DATA-02 vendor tokens encrypted at rest
        self.credentials = crypto.encrypt(json.dumps(data)) if data else ""

    def get_credentials(self) -> dict:
        import json

        return json.loads(crypto.decrypt(self.credentials)) if self.credentials else {}

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    @property
    def is_organisation_wide(self) -> bool:
        """True when nothing is linked — see this model's own docstring
        on why empty means everywhere."""
        return not self.customers.exists() and not self.accounts.exists()

    def covers(self, *, customer=None, account=None) -> bool:
        """Whether this connector applies to one Customer or Account.

        An account is covered by a connector linked to it *or* to one
        of its parent customers: connecting Zendesk to "Apple Inc"
        should cover Apple's regional accounts without naming each one,
        the same direction of travel record visibility already uses
        (services/customers/scoping.py). The reverse doesn't hold —
        linking one division doesn't put the parent company on
        Zendesk, because the other divisions may well be elsewhere."""

        if self.is_organisation_wide:
            return True
        if customer is not None:
            return self.customers.filter(pk=customer.pk).exists()
        if account is not None:
            return (
                self.accounts.filter(pk=account.pk).exists()
                or self.customers.filter(accounts=account).exists()
            )
        return False
