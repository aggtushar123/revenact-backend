from django.db import models

from services.accounts.models import Organisation


class FxRate(models.Model):
    """One admin-maintained "1 <currency> = <rate> <the org's own
    currency>" conversion, backing Settings > Currency's own Exchange
    Rates section (react-ts-app's CurrencyPage.tsx) — see
    services.customers.views.CustomerStatsView for the one place this
    actually gets used: converting a Customer's own contract currency
    (Customer.currency) into the org's reporting currency before
    summing MRR/ARR across customers that no longer share one currency.

    Deliberately manual entry, current rate only — no point-in-time
    history. Nothing in this app renders a real historical ARR-over-
    time trend from live backend data today, so storing a dated rate
    history would be infrastructure with no consumer yet; a live/
    current rate is used everywhere, which is a documented, deliberate
    simplification (see convert_to_org_currency's own docstring), not
    an oversight.

    Always "X -> the org's own current currency", never a free-form
    currency pair — there's exactly one thing every amount in this app
    ever needs converting into: whatever Organisation.currency is right
    now. That's also why changing Organisation.currency clears every
    row here (see OrganisationSettingsView) — a stored rate's meaning
    ("X -> the *old* base currency") doesn't carry over to a new base
    currency, and silently reinterpreting it would produce a wrong
    number with no visible sign anything was wrong.
    """

    organisation = models.ForeignKey(
        Organisation, related_name="fx_rates", on_delete=models.CASCADE
    )
    currency = models.CharField(max_length=3, choices=Organisation.Currency.choices)
    rate_to_org_currency = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="How many units of the org's own currency equal 1 unit of "
        "`currency`. FX rates need more precision than money amounts do, "
        "hence 6 decimal places rather than the usual 2.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["currency"]
        unique_together = ("organisation", "currency")

    def __str__(self):
        return f"1 {self.currency} = {self.rate_to_org_currency} ({self.organisation_id})"
