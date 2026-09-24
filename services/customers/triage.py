"""The Triage score: "who do I call today", computed once on the server.

A straight port of the frontend's `scoreRow` (`react-ts-app/src/pages/
dashboard/tabs/health-overview/triage.ts`), moved server-side so the Health
Overview dashboard's attention list and any other reader of it (the
attention-list endpoint) work from the same number instead of two
implementations of "what's wrong with this account" quietly drifting apart.

Four factors, each independent of the others:

- **Health.** How bad the current grade is — Poor costs three times what
  Average does, matching how much worse it is (see `SEVERITY`).
- **Pulse gap.** Only a *colder* AI read than the CSM's counts — the reverse
  is the CSM catching something first, not account risk.
- **Renewal.** Inside 90 days carries the full weight, 91-180 a reduced one.
  A negative day count (already overdue) is inside every window, so it scores
  as urgent rather than falling out the far end as "distant".
- **Trajectory.** Whether the last three months of `history` end worse than
  they started. Fewer than two months on record is "unknown", not "flat" —
  a naive first-vs-last comparison would read a single-entry trail as stable.

**The frontend's Pilot factor is dropped.** There `lifecycleStage === 'Pilot'`
added a flat +6, but this product's `Customer.LifecycleStage` has no Pilot
stage (see `services.customers.churn` for the same lesson learned the hard
way — its own "still in pilot" adjustment matched nothing until it was
rewritten to test the stored stage). Porting an adjustment that can never fire
would be dead weight, so `WEIGHT` simply has no `pilot` key.

Nothing here touches the database — `history` and every other input are
passed in, so a list endpoint that already has them (prefetched, ordered)
doesn't run a query per row for a rule that's pure values in, `Triage` out.
"""

from dataclasses import dataclass
from datetime import date

#: How much worse each grade is than Good. Drives both the base health score
#: and the trajectory delta, so a Good->Poor fall counts for more than a
#: Good->Average one.
SEVERITY = {"good": 0, "average": 1, "poor": 3}

WEIGHT = {
    "severity": 22,
    "pulse_gap": 11,
    "renewal_urgent": 18,
    "renewal_near": 8,
    "decline": 9,
}

#: Renewals inside this window carry the full renewal weighting.
RENEWAL_URGENT_DAYS = 90
#: Renewals inside this wider window carry a reduced weighting.
RENEWAL_NEAR_DAYS = 180

#: How many months of history triage judges direction over. An account that
#: slipped last quarter and has since held is not the same problem as one
#: that dropped a grade this month.
TRAJECTORY_WINDOW_MONTHS = 3

#: At or above this score an account is called out as needing action now.
ACTION_THRESHOLD = 40


@dataclass(frozen=True)
class Triage:
    score: int
    #: Every factor that contributed points, largest first. Empty for an
    #: account with no risk at all — it still scores 0, not "no data".
    factors: list[dict]
    direction: str
    #: Days from `today` to `renewal_date`, negative once it's passed. None
    #: when there's no renewal date to measure from.
    days_to_renewal: int | None


def triage(
    *,
    health_category: str | None,
    csm_pulse: int | None,
    ai_pulse: int | None,
    renewal_date: date | None,
    history: list[str],
    today: date,
) -> Triage:
    """Score one account for triage. Pure function, no ORM."""

    factors: list[dict] = []

    severity = SEVERITY.get(health_category, 0)
    if severity > 0:
        factors.append(
            {
                "label": f"Health is {health_category.capitalize()}",
                "points": severity * WEIGHT["severity"],
            }
        )

    # Only a *colder* AI read counts. The reverse (CSM below AI) is worth
    # surfacing too, but it isn't risk — it's the CSM catching something
    # first. Either side unrated means there's no gap at all, not a gap of 0.
    if csm_pulse is not None and ai_pulse is not None:
        pulse_gap = csm_pulse - ai_pulse
        if pulse_gap > 0:
            factors.append(
                {
                    "label": f"AI Pulse {pulse_gap} below CSM's",
                    "points": pulse_gap * WEIGHT["pulse_gap"],
                }
            )

    days_to_renewal = None if renewal_date is None else (renewal_date - today).days
    if days_to_renewal is not None and days_to_renewal <= RENEWAL_URGENT_DAYS:
        factors.append(
            {"label": f"Renews in {days_to_renewal}d", "points": WEIGHT["renewal_urgent"]}
        )
    elif days_to_renewal is not None and days_to_renewal <= RENEWAL_NEAR_DAYS:
        factors.append({"label": f"Renews in {days_to_renewal}d", "points": WEIGHT["renewal_near"]})

    trail = history[-TRAJECTORY_WINDOW_MONTHS:] if TRAJECTORY_WINDOW_MONTHS > 0 else list(history)
    if len(trail) < 2:
        direction = "unknown"
    else:
        delta = SEVERITY.get(trail[-1], 0) - SEVERITY.get(trail[0], 0)
        if delta > 0:
            direction = "declining"
            factors.append(
                {
                    "label": f"Fell {trail[0].capitalize()} → {trail[-1].capitalize()}",
                    "points": delta * WEIGHT["decline"],
                }
            )
        elif delta < 0:
            direction = "improving"
        else:
            direction = "flat"

    factors.sort(key=lambda factor: factor["points"], reverse=True)
    score = sum(factor["points"] for factor in factors)

    return Triage(
        score=score, factors=factors, direction=direction, days_to_renewal=days_to_renewal
    )
