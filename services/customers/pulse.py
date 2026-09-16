"""Account pulse: how the relationship feels right now, on a 1-5 scale.

Health (health.py) measures the structure of a relationship — adoption,
licences, whether anyone is in touch. Pulse measures its mood today: what
the model reads in the account's interactions, what the CSM says, how the
last month's conversations sound, how long since anyone spoke, how much is
broken. Five signals, each a reading from 1 (worst) to 5 (best), blended
by weight into one value and a label.

Two rules borrowed from the health rubric. A signal with nothing to
measure is excluded and the rest rescaled, so a thin CRM record is not
read as a sick account. And the breakdown says which signals counted.

The CSM's own pulse ages: full weight for a month, half weight up to
three months, then it no longer counts — a gut call from last quarter
should not outvote what is happening now.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CSM_FRESH_DAYS = 30
CSM_STALE_DAYS = 90
SENTIMENT_WINDOW_DAYS = 30
TOUCH_HORIZON_DAYS = 90
TICKET_CEILING = 10


@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    weight: Decimal


SIGNALS = (
    Signal("ai_pulse", "AI pulse", Decimal("3.0")),
    Signal("csm_pulse", "CSM pulse", Decimal("2.5")),
    Signal("sentiment", "Recent sentiment", Decimal("2.0")),
    Signal("touch", "Last contact", Decimal("1.5")),
    Signal("support", "Open tickets", Decimal("1.0")),
)
SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}

#: (floor, label, history category). Categories are the pulse-history dot
#: codes the tables already draw: 1 good, 3 warning, 2 bad, 0 no signal.
LABELS = (
    (Decimal("4.5"), "Thriving", 1),
    (Decimal("3.5"), "Healthy", 1),
    (Decimal("2.5"), "Watch", 3),
    (Decimal("1.5"), "At risk", 2),
    (Decimal("0"), "Critical", 2),
)
NO_SIGNAL = ("No signal", 0)


@dataclass(frozen=True)
class Reading:
    key: str
    label: str
    #: The weight actually applied (the CSM pulse's halves as it ages).
    weight: Decimal
    #: 1-5 to a tenth, or None when there was nothing to measure.
    reading: Decimal | None
    #: What the reading was made from, for the tooltip.
    note: str

    @property
    def available(self):
        return self.reading is not None


@dataclass(frozen=True)
class Pulse:
    value: Decimal | None
    label: str
    category: int
    readings: list[Reading]

    def as_payload(self):
        return {
            "value": str(self.value) if self.value is not None else None,
            "label": self.label,
            "category": self.category,
            "breakdown": [
                {
                    "key": r.key,
                    "label": r.label,
                    "weight": str(r.weight),
                    "reading": str(r.reading) if r.reading is not None else None,
                    "note": r.note,
                }
                for r in self.readings
            ],
        }


def _tenth(value):
    return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _scaled(ratio):
    """0..1 → 1..5, to a tenth."""
    return None if ratio is None else _tenth(Decimal("1") + Decimal("4") * Decimal(str(ratio)))


def _ratio_from_reading(reading):
    return (reading - Decimal("1")) / Decimal("4")


def compute(
    *,
    ai_pulse_value,
    csm_pulse_score,
    csm_pulse_age_days,
    positive_count,
    negative_count,
    classified_count,
    days_since_touch,
    open_ticket_count,
):
    """Score every signal from already-loaded values. Runs no queries."""
    readings = []

    ai = SIGNALS_BY_KEY["ai_pulse"]
    if ai_pulse_value:
        readings.append(
            Reading(ai.key, ai.label, ai.weight, _tenth(ai_pulse_value), "what the model reads")
        )
    else:
        readings.append(Reading(ai.key, ai.label, ai.weight, None, "not scored yet"))

    csm = SIGNALS_BY_KEY["csm_pulse"]
    if not csm_pulse_score:
        readings.append(Reading(csm.key, csm.label, csm.weight, None, "not set"))
    elif csm_pulse_age_days is None or csm_pulse_age_days <= CSM_FRESH_DAYS:
        when = "" if csm_pulse_age_days is None else f", set {csm_pulse_age_days} days ago"
        readings.append(
            Reading(
                csm.key, csm.label, csm.weight, _tenth(csm_pulse_score), f"the CSM's call{when}"
            )
        )
    elif csm_pulse_age_days <= CSM_STALE_DAYS:
        readings.append(
            Reading(
                csm.key,
                csm.label,
                csm.weight / 2,
                _tenth(csm_pulse_score),
                f"set {csm_pulse_age_days} days ago, so half weight",
            )
        )
    else:
        readings.append(
            Reading(
                csm.key,
                csm.label,
                csm.weight,
                None,
                f"set {csm_pulse_age_days} days ago, too old to count",
            )
        )

    sentiment = SIGNALS_BY_KEY["sentiment"]
    if classified_count:
        share = (positive_count - negative_count) / classified_count
        readings.append(
            Reading(
                sentiment.key,
                sentiment.label,
                sentiment.weight,
                _scaled((share + 1) / 2),
                f"{positive_count} positive, {negative_count} negative of "
                f"{classified_count} in the last {SENTIMENT_WINDOW_DAYS} days",
            )
        )
    else:
        readings.append(
            Reading(
                sentiment.key,
                sentiment.label,
                sentiment.weight,
                None,
                f"no conversations in the last {SENTIMENT_WINDOW_DAYS} days",
            )
        )

    touch = SIGNALS_BY_KEY["touch"]
    if days_since_touch is None:
        readings.append(Reading(touch.key, touch.label, touch.weight, None, "no contact on record"))
    else:
        ratio = 1 - min(max(days_since_touch, 0), TOUCH_HORIZON_DAYS) / TOUCH_HORIZON_DAYS
        when = "today" if days_since_touch == 0 else f"{days_since_touch} days ago"
        readings.append(Reading(touch.key, touch.label, touch.weight, _scaled(ratio), when))

    support = SIGNALS_BY_KEY["support"]
    open_count = open_ticket_count or 0
    readings.append(
        Reading(
            support.key,
            support.label,
            support.weight,
            _scaled(1 - min(open_count, TICKET_CEILING) / TICKET_CEILING),
            f"{open_count} open",
        )
    )

    measured = [r for r in readings if r.available]
    if not measured:
        return Pulse(None, NO_SIGNAL[0], NO_SIGNAL[1], readings)
    total_weight = sum((r.weight for r in measured), Decimal("0"))
    blended = sum((r.weight * _ratio_from_reading(r.reading) for r in measured), Decimal("0"))
    value = _tenth(Decimal("1") + Decimal("4") * blended / total_weight)
    for floor, label, category in LABELS:
        if value >= floor:
            return Pulse(value, label, category, readings)
    return Pulse(value, LABELS[-1][1], LABELS[-1][2], readings)
