"""The health rubric: what `health_score` is actually made of.

`health_score` used to be a stored number somebody typed, and the frontend's
HealthPopover invented a five-row "breakdown" of it by branching on the number
itself — so the parts were derived from the total rather than the total from the
parts, and they never added up to it. This module is the real calculation, in
the direction it should run.

Five components, weighted out of 10. The weights are the ones the popover was
already showing, so the rubric matches the decision someone had already made:

    Customer Touch       4.0
    AI Pulse             2.0
    Licence Utilization  2.0
    Adoption             1.5
    Support Tickets      0.5
                        ────
                        10.0

Every component returns a ratio in 0..1 plus whether it could be measured at
all. **Components with no data are excluded and the remainder is rescaled**,
rather than scored zero: a customer with no seat counts recorded is not a
customer with no seats in use, and punishing an empty field would make the
score a measure of how completely the CRM was filled in.

Nothing here touches the database. The two related-row inputs (last touch,
open ticket count) are passed in, so a list endpoint can annotate them once for
the whole page instead of running two queries per row — see
`with_health_inputs`.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

# --- Component tuning -------------------------------------------------------

#: Days since the last touch at which Customer Touch reaches zero. Decays
#: linearly to it rather than stepping, so a score doesn't lurch on a boundary.
TOUCH_HORIZON_DAYS = 90

#: Products (primary + additional) counted as full adoption breadth.
ADOPTION_TARGET_PRODUCTS = 4

#: Open tickets at which Support Tickets Volume reaches zero.
#:
#: Absolute rather than per-seat. Normalising by contracted seats would be more
#: correct — ten open tickets means something different at 20 seats than at 560
#: — but this component carries 0.5 of 10, and a rule nobody can explain in a
#: QBR is a worse trade at that weight. Revisit if the weight ever grows.
TICKET_CEILING = 20

#: The AI pulse scale, used to normalise 1-5 onto 0..1.
AI_PULSE_MIN, AI_PULSE_MAX = 1, 5

MAX_SCORE = Decimal("10.0")


@dataclass(frozen=True)
class Component:
    key: str
    #: The label the tooltip shows. Matches what HealthPopover already said.
    label: str
    weight: Decimal


COMPONENTS = (
    Component("customer_touch", "Customer Touch", Decimal("4.0")),
    Component("ai_pulse", "AI Pulse", Decimal("2.0")),
    Component("licence_utilization", "Licence Utilization", Decimal("2.0")),
    Component("adoption", "Aggregate Adoption Score", Decimal("1.5")),
    Component("support_tickets", "Support Tickets Volume", Decimal("0.5")),
)

COMPONENTS_BY_KEY = {c.key: c for c in COMPONENTS}

assert sum(c.weight for c in COMPONENTS) == MAX_SCORE, "weights must total 10"


@dataclass(frozen=True)
class ComponentScore:
    key: str
    label: str
    weight: Decimal
    #: 0..1, or None when this component has nothing to measure.
    ratio: float | None
    #: Points earned out of `weight`, rounded to a tenth. Zero for an
    #: unmeasurable component — which is not the same as scoring zero: see
    #: `score_from`, where an unavailable component is dropped from the
    #: denominator too. Assigned across the whole breakdown rather than per
    #: component, so the displayed tenths still add up — see `_allocate_points`.
    points: Decimal

    @property
    def available(self):
        return self.ratio is not None


def _round(value):
    return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _clamp01(value):
    return max(0.0, min(1.0, value))


def _exact_points(weight, ratio):
    return weight * Decimal(str(ratio))


def _allocate_points(weights_and_ratios):
    """Round each component's points to a tenth *without* losing the total.

    Rounding each one on its own is what a breakdown normally does, and it
    quietly breaks the one promise this feature makes: five components rounded
    up independently summed to 5.5 under a headline of 5.4. Largest-remainder
    apportionment instead — floor everything, then hand the leftover tenths to
    the components with the largest fractional parts — so the rounded points
    always total the rounded sum of the exact ones.

    Takes and returns values in breakdown order; unmeasurable components
    (ratio None) get no points and take no part in the apportionment.
    """
    exact = [
        _exact_points(weight, ratio) if ratio is not None else None
        for weight, ratio in weights_and_ratios
    ]
    measured = [i for i, value in enumerate(exact) if value is not None]

    points = [Decimal("0.0")] * len(exact)
    if not measured:
        return points

    target = _round(sum(exact[i] for i in measured))
    for i in measured:
        points[i] = exact[i].quantize(Decimal("0.1"), rounding=ROUND_DOWN)

    leftover = target - sum(points[i] for i in measured)
    steps = int((leftover / Decimal("0.1")).to_integral_value())

    # Biggest fractional part first; ties fall to the heavier component, then
    # to breakdown order, so the same inputs always apportion the same way.
    ranked = sorted(
        measured,
        key=lambda i: (exact[i] - points[i], weights_and_ratios[i][0], -i),
        reverse=True,
    )
    for i in ranked[:steps]:
        points[i] += Decimal("0.1")

    return points


# --- The components ---------------------------------------------------------


def _customer_touch(*, days_since_touch):
    """How recently anyone touched this customer.

    `days_since_touch` is measured from the most recent Activity, or from when
    the row was created if it has none yet — a customer onboarded last week has
    not been neglected, and scoring it zero on the heaviest component would bury
    every new logo at the bottom of the book.
    """
    if days_since_touch is None:
        return None
    return _clamp01(1 - (days_since_touch / TOUCH_HORIZON_DAYS))


def _ai_pulse(*, ai_pulse_value):
    """The model's own 1-5 read, normalised. Null when it hasn't scored this one."""
    if ai_pulse_value is None:
        return None
    span = AI_PULSE_MAX - AI_PULSE_MIN
    return _clamp01((ai_pulse_value - AI_PULSE_MIN) / span)


def _licence_utilization(*, active_seats, contracted_seats):
    """Seats in use against seats paid for.

    Capped at 1.0: being over-subscribed is an expansion conversation, not extra
    health. Unmeasurable without a contracted figure to compare against.
    """
    if not contracted_seats or active_seats is None:
        return None
    return _clamp01(active_seats / contracted_seats)


def _adoption(*, primary_product, additional_products_count):
    """Breadth of the product actually in use.

    Counts the primary product plus any additional ones against a target
    breadth. Unmeasurable when neither field has been filled in — as opposed to
    a recorded zero, which really does mean no products.
    """
    if not primary_product and additional_products_count is None:
        return None
    products = (1 if primary_product else 0) + (additional_products_count or 0)
    return _clamp01(products / ADOPTION_TARGET_PRODUCTS)


def _support_tickets(*, open_ticket_count):
    """Fewer open tickets is healthier, to a floor at TICKET_CEILING.

    Counts open ones rather than all-time volume: a customer who raised twenty
    tickets and had them all resolved is a well-supported customer, not a sick
    one.
    """
    if open_ticket_count is None:
        return None
    return _clamp01(1 - (open_ticket_count / TICKET_CEILING))


# --- Putting it together ----------------------------------------------------


def breakdown_from(
    *,
    days_since_touch,
    ai_pulse_value,
    active_seats,
    contracted_seats,
    primary_product,
    additional_products_count,
    open_ticket_count,
):
    """Score every component from already-loaded values. Runs no queries."""
    ratios = {
        "customer_touch": _customer_touch(days_since_touch=days_since_touch),
        "ai_pulse": _ai_pulse(ai_pulse_value=ai_pulse_value),
        "licence_utilization": _licence_utilization(
            active_seats=active_seats, contracted_seats=contracted_seats
        ),
        "adoption": _adoption(
            primary_product=primary_product,
            additional_products_count=additional_products_count,
        ),
        "support_tickets": _support_tickets(open_ticket_count=open_ticket_count),
    }
    points = _allocate_points([(c.weight, ratios[c.key]) for c in COMPONENTS])
    return [
        ComponentScore(
            key=c.key,
            label=c.label,
            weight=c.weight,
            ratio=ratios[c.key],
            points=points[i],
        )
        for i, c in enumerate(COMPONENTS)
    ]


def score_from(breakdown):
    """The 0-10 score for a breakdown, or None if nothing could be measured.

    Rescales to the weight that was actually measurable, so a customer scored on
    three of five components is marked out of those three rather than losing the
    other two outright.
    """
    measured = [c for c in breakdown if c.available]
    if not measured:
        return None

    earned = sum((_exact_points(c.weight, c.ratio) for c in measured), Decimal("0"))
    available_weight = sum((c.weight for c in measured), Decimal("0"))

    # When everything was measurable the rescale is a no-op, and the score is
    # exactly the sum of the points above. When it isn't, the score is marked
    # out of the weight that could be measured, so it deliberately sits above
    # that sum — callers showing both must say which.
    return _round(MAX_SCORE * earned / available_weight)
