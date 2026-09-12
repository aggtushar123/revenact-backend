from decimal import Decimal

from .models import FxRate


def rates_for(organisation) -> dict[str, Decimal]:
    """Every configured rate for one organisation, as `{currency: rate}`.

    For callers converting a whole page of rows: pass the result to
    `convert_to_org_currency` as `rates` and the conversion costs one query for
    the request instead of one per row. A list endpoint returning 500 customers
    in three currencies would otherwise issue 500 `FxRate` lookups to draw one
    chart.
    """
    return dict(
        FxRate.objects.filter(organisation=organisation).values_list(
            "currency", "rate_to_org_currency"
        )
    )


def convert_to_org_currency(
    amount: Decimal, from_currency: str, organisation, rates: dict[str, Decimal] | None = None
) -> Decimal | None:
    """Converts `amount` (denominated in `from_currency`) into
    `organisation`'s own currency using its admin-maintained FxRate
    table — the one hook point every rollup that sums money across
    Customers with different currencies calls into (see
    services.customers.views.CustomerStatsView), so a currency-changes-
    and-rates-go-stale bug can't hide in more than one place.

    Returns `amount` unchanged when `from_currency` already *is* the
    org's own currency (the common case — no conversion needed).
    Returns `None` when `from_currency` differs and no FxRate is
    configured for it: the caller's job to decide what "no rate" means
    for its own rollup (CustomerStatsView excludes that customer's
    money from the sum but still counts it, rather than silently
    treating an unconverted amount as if it were already in the org's
    currency — see that view's own comment).

    Uses whatever rate is currently stored — no point-in-time history,
    see FxRate's own docstring for why that's a deliberate, documented
    simplification rather than an oversight.

    `rates` is an optional pre-fetched `{currency: rate}` table (see
    `rates_for`) for callers converting many rows at once. Same rule either
    way — the lookup is the only thing that moves — so the "no rate means no
    number" decision above still lives in exactly one place.
    """
    if from_currency == organisation.currency:
        return amount

    if rates is not None:
        rate_value = rates.get(from_currency)
        return None if rate_value is None else amount * rate_value

    rate = FxRate.objects.filter(organisation=organisation, currency=from_currency).first()
    if rate is None:
        return None
    return amount * rate.rate_to_org_currency
