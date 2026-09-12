from django.db import models

from services.accounts.models import Organisation


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

    # What this is not

    Not a live integration. There is no OAuth, no API client, no sync —
    nothing here reaches out to Zendesk. It records *that* an
    organisation uses a system and *which* companies it covers, so
    tickets already in this database can be attributed to it. A real
    sync needs vendor credentials and a task queue, neither of which
    this codebase has (see services/webhooks/models.py's own note on
    the missing queue).

    That's why there's no `credentials` or `api_key` field: storing a
    secret nothing authenticates with would be a liability with no
    benefit."""

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

    organisation = models.ForeignKey(
        Organisation, related_name="connectors", on_delete=models.CASCADE
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
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
