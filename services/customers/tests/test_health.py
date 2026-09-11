"""Unit tier: the health rubric in isolation, no DB and no HTTP.

`health.py` runs no queries by design, so everything here is plain values in,
scores out — the model/API wiring is covered in test_models.py and test_views.py.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from services.customers.health import (
    ADOPTION_TARGET_PRODUCTS,
    COMPONENTS,
    MAX_SCORE,
    TICKET_CEILING,
    TOUCH_HORIZON_DAYS,
    breakdown_from,
    score_from,
)

#: Inputs that score full marks on every component, so a test can vary one.
PERFECT = {
    "days_since_touch": 0,
    "ai_pulse_value": 5,
    "active_seats": 100,
    "contracted_seats": 100,
    "primary_product": "Product A",
    "additional_products_count": ADOPTION_TARGET_PRODUCTS - 1,
    "open_ticket_count": 0,
}


def component(breakdown, key):
    return next(c for c in breakdown if c.key == key)


def score(**overrides):
    return score_from(breakdown_from(**{**PERFECT, **overrides}))


def ratio(key, **overrides):
    return component(breakdown_from(**{**PERFECT, **overrides}), key).ratio


class WeightTests(SimpleTestCase):
    def test_the_weights_total_ten(self):
        self.assertEqual(sum(c.weight for c in COMPONENTS), MAX_SCORE)

    def test_customer_touch_carries_the_most(self):
        # The popover's own split, which the rubric was built to match.
        weights = {c.key: c.weight for c in COMPONENTS}
        self.assertEqual(weights["customer_touch"], Decimal("4.0"))
        self.assertEqual(max(weights.values()), weights["customer_touch"])


class CustomerTouchTests(SimpleTestCase):
    def test_decays_linearly_to_the_horizon(self):
        self.assertEqual(ratio("customer_touch", days_since_touch=0), 1.0)
        self.assertAlmostEqual(
            ratio("customer_touch", days_since_touch=TOUCH_HORIZON_DAYS // 2), 0.5, places=2
        )
        self.assertEqual(ratio("customer_touch", days_since_touch=TOUCH_HORIZON_DAYS), 0.0)

    def test_does_not_go_negative_past_the_horizon(self):
        self.assertEqual(ratio("customer_touch", days_since_touch=TOUCH_HORIZON_DAYS * 4), 0.0)

    def test_is_unmeasurable_without_a_date(self):
        self.assertIsNone(ratio("customer_touch", days_since_touch=None))


class AIPulseTests(SimpleTestCase):
    def test_maps_the_scale_onto_zero_to_one(self):
        self.assertEqual(ratio("ai_pulse", ai_pulse_value=5), 1.0)
        self.assertEqual(ratio("ai_pulse", ai_pulse_value=3), 0.5)
        # 1 is the bottom of the scale, so it earns nothing — but it is a real
        # reading, unlike None below.
        self.assertEqual(ratio("ai_pulse", ai_pulse_value=1), 0.0)

    def test_unscored_is_unmeasurable_not_zero(self):
        self.assertIsNone(ratio("ai_pulse", ai_pulse_value=None))


class LicenceUtilizationTests(SimpleTestCase):
    def test_is_the_share_of_contracted_seats_in_use(self):
        self.assertEqual(ratio("licence_utilization", active_seats=50, contracted_seats=100), 0.5)

    def test_over_subscription_is_not_extra_credit(self):
        self.assertEqual(ratio("licence_utilization", active_seats=200, contracted_seats=100), 1.0)

    def test_is_unmeasurable_without_both_figures(self):
        self.assertIsNone(ratio("licence_utilization", contracted_seats=None))
        self.assertIsNone(ratio("licence_utilization", active_seats=None))
        # Zero contracted seats can't be a denominator either.
        self.assertIsNone(ratio("licence_utilization", contracted_seats=0))

    def test_zero_seats_in_use_is_a_real_zero(self):
        self.assertEqual(ratio("licence_utilization", active_seats=0, contracted_seats=100), 0.0)


class AdoptionTests(SimpleTestCase):
    def test_counts_the_primary_product_plus_the_extras(self):
        self.assertEqual(
            ratio("adoption", primary_product="A", additional_products_count=1),
            2 / ADOPTION_TARGET_PRODUCTS,
        )

    def test_caps_at_the_target_breadth(self):
        self.assertEqual(
            ratio("adoption", primary_product="A", additional_products_count=99), 1.0
        )

    def test_is_unmeasurable_only_when_neither_field_is_filled_in(self):
        self.assertIsNone(ratio("adoption", primary_product="", additional_products_count=None))
        # A recorded zero really does mean no extra products, so it scores.
        self.assertEqual(
            ratio("adoption", primary_product="", additional_products_count=0), 0.0
        )


class SupportTicketTests(SimpleTestCase):
    def test_fewer_open_tickets_scores_higher(self):
        self.assertEqual(ratio("support_tickets", open_ticket_count=0), 1.0)
        self.assertEqual(ratio("support_tickets", open_ticket_count=TICKET_CEILING // 2), 0.5)
        self.assertEqual(ratio("support_tickets", open_ticket_count=TICKET_CEILING), 0.0)

    def test_does_not_go_negative(self):
        self.assertEqual(ratio("support_tickets", open_ticket_count=TICKET_CEILING * 3), 0.0)


class ScoreTests(SimpleTestCase):
    def test_full_marks_everywhere_is_ten(self):
        self.assertEqual(score(), Decimal("10.0"))

    def test_nothing_anywhere_is_zero(self):
        self.assertEqual(
            score(
                days_since_touch=TOUCH_HORIZON_DAYS,
                ai_pulse_value=1,
                active_seats=0,
                primary_product="",
                additional_products_count=0,
                open_ticket_count=TICKET_CEILING,
            ),
            Decimal("0.0"),
        )

    def test_a_component_contributes_its_own_weight(self):
        # AI Pulse is worth 2.0; halving it should cost exactly 1.0.
        self.assertEqual(score(ai_pulse_value=3), Decimal("9.0"))

    def test_customer_touch_moves_the_score_most(self):
        touch = MAX_SCORE - score(days_since_touch=TOUCH_HORIZON_DAYS)
        tickets = MAX_SCORE - score(open_ticket_count=TICKET_CEILING)
        self.assertGreater(touch, tickets)
        self.assertEqual(touch, Decimal("4.0"))
        self.assertEqual(tickets, Decimal("0.5"))

    def test_an_unmeasurable_component_is_excluded_not_scored_zero(self):
        # A customer with no seat figures is not a customer with no seats in
        # use. Dropping the component keeps the rest marked out of what's left,
        # so the score stays 10 rather than falling to 8.
        self.assertEqual(score(contracted_seats=None), Decimal("10.0"))

    def test_rescales_to_the_weight_actually_measured(self):
        # Only Customer Touch (4.0) and Support Tickets (0.5) are measurable;
        # touch is perfect and tickets are at the floor, so the score is
        # 10 * 4.0 / 4.5.
        result = score(
            ai_pulse_value=None,
            contracted_seats=None,
            primary_product="",
            additional_products_count=None,
            open_ticket_count=TICKET_CEILING,
        )
        self.assertEqual(result, Decimal("8.9"))

    def test_is_none_when_nothing_can_be_measured(self):
        self.assertIsNone(
            score(
                days_since_touch=None,
                ai_pulse_value=None,
                contracted_seats=None,
                primary_product="",
                additional_products_count=None,
                open_ticket_count=None,
            )
        )


class BreakdownShapeTests(SimpleTestCase):
    def test_returns_every_component_in_weight_order(self):
        breakdown = breakdown_from(**PERFECT)
        self.assertEqual([c.key for c in breakdown], [c.key for c in COMPONENTS])

    def test_points_are_out_of_the_components_own_weight(self):
        breakdown = breakdown_from(**{**PERFECT, "ai_pulse_value": 3})
        self.assertEqual(component(breakdown, "ai_pulse").points, Decimal("1.0"))
        self.assertEqual(component(breakdown, "ai_pulse").weight, Decimal("2.0"))

    def test_an_unavailable_component_reports_no_points_and_says_so(self):
        breakdown = breakdown_from(**{**PERFECT, "contracted_seats": None})
        licence = component(breakdown, "licence_utilization")
        self.assertFalse(licence.available)
        self.assertEqual(licence.points, Decimal("0.0"))

    def test_the_points_of_a_fully_measured_breakdown_sum_to_its_score(self):
        # The property the old popover could never have: the parts add up to
        # the total, because the total is computed from the parts.
        inputs = {**PERFECT, "ai_pulse_value": 4, "open_ticket_count": 5, "active_seats": 80}
        breakdown = breakdown_from(**inputs)
        self.assertEqual(sum(c.points for c in breakdown), score_from(breakdown))

    def test_points_still_sum_when_every_component_rounds_up(self):
        # Regression: this exact customer rendered five components summing to
        # 5.5 under a headline of 5.4, because each was rounded on its own.
        breakdown = breakdown_from(
            days_since_touch=55,
            ai_pulse_value=4,
            active_seats=790,
            contracted_seats=1000,
            primary_product="Product A",
            additional_products_count=0,
            open_ticket_count=5,
        )
        self.assertEqual(sum(c.points for c in breakdown), score_from(breakdown))

    def test_points_sum_across_a_wide_sweep_of_inputs(self):
        # Rounding drift only shows up on particular combinations, so sweep
        # rather than trust a handful of hand-picked cases.
        for touch in range(0, 91, 7):
            for seats in range(0, 101, 11):
                for tickets in range(0, 21, 3):
                    breakdown = breakdown_from(
                        days_since_touch=touch,
                        ai_pulse_value=(touch % 5) + 1,
                        active_seats=seats,
                        contracted_seats=100,
                        primary_product="Product A",
                        additional_products_count=seats % 4,
                        open_ticket_count=tickets,
                    )
                    self.assertEqual(
                        sum(c.points for c in breakdown),
                        score_from(breakdown),
                        msg=f"touch={touch} seats={seats} tickets={tickets}",
                    )

    def test_an_unmeasured_component_makes_the_score_exceed_the_visible_points(self):
        # Deliberate, not drift: the score is marked out of the weight that
        # could be measured, so the rows on screen total less than the headline.
        breakdown = breakdown_from(**{**PERFECT, "contracted_seats": None})
        self.assertEqual(sum(c.points for c in breakdown), Decimal("8.0"))
        self.assertEqual(score_from(breakdown), Decimal("10.0"))
