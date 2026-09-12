"""How likely a renewal is to be lost, as a plain stated assumption.

**A business rule, not a model.** Nothing here was fitted to historical churn —
this product has no churn history to fit to. It writes down the judgement a CS
leader already applies in a forecast review, so that every screen applies it the
same way and so it can be argued with in one place.

That last part is why it lives in Python rather than in the browser. It started
in the Renewal Date tab's own `renewal.ts`, which was fine while one screen used
it; the Revenue Forecast needs the same number, and two implementations of a
churn model that both drive money on screen is a discrepancy with a date on it.

Base rate by current health, then three adjustments, each for something that
changes the odds independently of the grade:

- **No recent contact.** A renewal you haven't discussed is a renewal you are
  not in. Worth more than the pulse disagreement below, because it is a fact
  rather than an opinion.
- **The two pulses disagree by 2+.** Somebody is wrong about this account, and
  being wrong about one inside its renewal window is itself a risk — the
  Divergence tab exists for the same reason.
- **Not yet embedded.** An account still onboarding or in kickoff has less to
  walk away from.

Capped below 1: a renewal is never *certain* to be lost while it is still open,
and a 100% line item invites people to stop working it.
"""

from .models import Customer

#: `health_category` is a derived property rather than a field, so there is no
#: `get_..._display()` for it — the labels come from the choices directly.
HEALTH_LABELS = dict(Customer.HealthCategory.choices)

#: Starting probability by health grade.
BASE_RISK = {
    Customer.HealthCategory.GOOD: 0.05,
    Customer.HealthCategory.AVERAGE: 0.25,
    Customer.HealthCategory.POOR: 0.5,
}

RISK_ADJUSTMENTS = {
    "cold_contact": 0.1,
    "pulse_disagreement": 0.1,
    "not_embedded": 0.05,
}

MAX_RISK = 0.9

#: Days since the last logged activity past which an account counts as cold.
#: Two months — the point at which a renewal conversation starts from scratch
#: rather than continues.
CONTACT_COLD_DAYS = 60

#: How far apart the two pulses have to be to count as disagreement. One point
#: is rounding on a five-point scale; two is a difference of opinion. Matches
#: the Divergence tab's own threshold.
PULSE_GAP_THRESHOLD = 2

#: Stages where the customer hasn't landed yet. By stored value, never by
#: label: the label is a thing customers rename, and this rule previously
#: tested one for the word "pilot" — a stage this product does not have, which
#: made the adjustment worth five points to nobody.
NOT_EMBEDDED_STAGES = {Customer.LifecycleStage.ONBOARDING, Customer.LifecycleStage.KICKOFF}


def risk_of_loss(customer, *, days_since_touch=None):
    """`(risk, factors)` for one customer.

    `factors` is every contribution with its label, because a ranking nobody
    can interrogate is a ranking nobody acts on — the Renewal tab prints them
    on each row.

    `days_since_touch` is passed in rather than read, so a list endpoint that
    already annotated it (`with_health_inputs`) doesn't pay for the query
    again; None means unknown, which is not the same as stale.
    """

    base = BASE_RISK.get(customer.health_category, 0.25)
    factors = [
        {
            "label": f"{HEALTH_LABELS.get(customer.health_category, 'Unrated')} health",
            "points": base,
        },
    ]

    if days_since_touch is not None and days_since_touch > CONTACT_COLD_DAYS:
        factors.append(
            {
                "label": f"No contact in {days_since_touch} days",
                "points": RISK_ADJUSTMENTS["cold_contact"],
            }
        )

    csm, ai = customer.csm_pulse_score, customer.ai_pulse_value
    if csm is not None and ai is not None and abs(csm - ai) >= PULSE_GAP_THRESHOLD:
        factors.append(
            {
                "label": "CSM and AI pulse disagree",
                "points": RISK_ADJUSTMENTS["pulse_disagreement"],
            }
        )

    if customer.lifecycle_stage in NOT_EMBEDDED_STAGES:
        factors.append(
            {
                "label": f"Still in {customer.get_lifecycle_stage_display().lower()}",
                "points": RISK_ADJUSTMENTS["not_embedded"],
            }
        )

    risk = min(MAX_RISK, sum(factor["points"] for factor in factors))
    return round(risk, 4), factors
