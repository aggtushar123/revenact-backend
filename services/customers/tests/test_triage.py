"""Unit tier: the Triage score in isolation, no DB and no HTTP.

`triage.py` runs no queries by design — it's a pure port of the frontend's
`triage.ts` (`react-ts-app/src/pages/dashboard/tabs/health-overview/triage.ts`),
so everything here is plain values in, a `Triage` out. The three tests marked
"mirrors triage.test.ts" reproduce assertions from that file's suite verbatim
(translated to this module's argument shape) so the port is provably the same
rule, not just a similar one.
"""

from datetime import date

from django.test import SimpleTestCase

from services.customers.triage import ACTION_THRESHOLD, WEIGHT, triage

TODAY = date(2026, 6, 15)


def score(
    *,
    health_category=None,
    csm_pulse=None,
    ai_pulse=None,
    renewal_date=None,
    history=(),
    today=TODAY,
):
    return triage(
        health_category=health_category,
        csm_pulse=csm_pulse,
        ai_pulse=ai_pulse,
        renewal_date=renewal_date,
        history=list(history),
        today=today,
    )


class HealthFactorTests(SimpleTestCase):
    def test_good_health_carries_no_factor(self):
        t = score(health_category="good")
        self.assertEqual(t.score, 0)
        self.assertEqual(t.factors, [])

    def test_average_health_scores_the_severity_weight(self):
        t = score(health_category="average")
        self.assertEqual(t.score, 22)
        self.assertEqual(t.factors, [{"label": "Health is Average", "points": 22}])

    def test_poor_health_scores_three_times_the_severity_weight(self):
        t = score(health_category="poor")
        self.assertEqual(t.score, 66)
        self.assertEqual(t.factors, [{"label": "Health is Poor", "points": 66}])

    def test_unrated_health_carries_no_factor(self):
        t = score(health_category=None)
        self.assertEqual(t.score, 0)
        self.assertEqual(t.factors, [])


class PulseGapFactorTests(SimpleTestCase):
    def test_no_gap_carries_no_factor(self):
        self.assertEqual(score(csm_pulse=3, ai_pulse=3).factors, [])

    def test_a_one_point_gap_scores_the_pulse_gap_weight(self):
        t = score(csm_pulse=3, ai_pulse=2)
        self.assertEqual(t.factors, [{"label": "AI Pulse 1 below CSM's", "points": 11}])

    def test_a_two_point_gap_doubles_it(self):
        t = score(csm_pulse=5, ai_pulse=3)
        self.assertEqual(t.factors, [{"label": "AI Pulse 2 below CSM's", "points": 22}])

    def test_a_colder_csm_read_is_not_charged(self):
        # The reverse gap is a signal, but it isn't account risk — mirrors
        # "only charges for a colder AI read, not a colder CSM read" below.
        self.assertEqual(score(csm_pulse=2, ai_pulse=5).factors, [])

    def test_either_side_unrated_carries_no_factor(self):
        self.assertEqual(score(csm_pulse=None, ai_pulse=2).factors, [])
        self.assertEqual(score(csm_pulse=4, ai_pulse=None).factors, [])


class RenewalFactorTests(SimpleTestCase):
    def test_ninety_days_out_is_still_urgent(self):
        # 90 days out, inclusive boundary.
        renewal = date(2026, 9, 13)
        t = score(renewal_date=renewal)
        self.assertEqual((renewal - TODAY).days, 90)
        self.assertEqual(t.factors, [{"label": "Renews in 90d", "points": 18}])

    def test_ninety_one_days_out_drops_to_the_near_weight(self):
        renewal = date(2026, 9, 14)
        self.assertEqual((renewal - TODAY).days, 91)
        t = score(renewal_date=renewal)
        self.assertEqual(t.factors, [{"label": "Renews in 91d", "points": 8}])

    def test_one_hundred_eighty_days_out_is_still_near(self):
        renewal = date(2026, 12, 12)
        self.assertEqual((renewal - TODAY).days, 180)
        t = score(renewal_date=renewal)
        self.assertEqual(t.factors, [{"label": "Renews in 180d", "points": 8}])

    def test_one_hundred_eighty_one_days_out_carries_no_factor(self):
        renewal = date(2026, 12, 13)
        self.assertEqual((renewal - TODAY).days, 181)
        self.assertEqual(score(renewal_date=renewal).factors, [])

    def test_an_overdue_renewal_is_still_urgent(self):
        # 5 days overdue — inside every urgency window, same as the frontend's
        # "counts a past renewal as urgent, not as distant".
        renewal = date(2026, 6, 10)
        self.assertEqual((renewal - TODAY).days, -5)
        t = score(renewal_date=renewal)
        self.assertEqual(t.factors, [{"label": "Renews in -5d", "points": 18}])
        self.assertEqual(t.days_to_renewal, -5)

    def test_no_renewal_date_carries_no_factor_and_is_null(self):
        t = score(renewal_date=None)
        self.assertEqual(t.factors, [])
        self.assertIsNone(t.days_to_renewal)


class TrajectoryFactorTests(SimpleTestCase):
    def test_fewer_than_two_months_is_unknown(self):
        self.assertEqual(score(history=["good"]).direction, "unknown")
        self.assertEqual(score(history=[]).direction, "unknown")
        self.assertEqual(score(history=["good"]).factors, [])

    def test_a_three_month_fall_scores_the_full_delta(self):
        t = score(history=["good", "average", "poor"])
        self.assertEqual(t.direction, "declining")
        self.assertEqual(t.factors, [{"label": "Fell Good → Poor", "points": 27}])

    def test_an_improving_trail_carries_no_factor(self):
        t = score(history=["poor", "good"])
        self.assertEqual(t.direction, "improving")
        self.assertEqual(t.factors, [])

    def test_a_flat_trail_carries_no_factor(self):
        t = score(history=["good", "poor", "good"])
        self.assertEqual(t.direction, "flat")
        self.assertEqual(t.factors, [])

    def test_only_the_last_three_months_are_judged(self):
        # A year-old slip that has since held is not today's problem — mirrors
        # the frontend's TRAJECTORY_WINDOW_MONTHS behaviour.
        t = score(history=["poor", "good", "good", "good"])
        self.assertEqual(t.direction, "flat")
        self.assertEqual(t.factors, [])


class CombinedScoreTests(SimpleTestCase):
    def test_scores_exactly_the_action_threshold(self):
        # Average health (22) + a renewal 30 days out (18) = 40.
        t = score(health_category="average", renewal_date=date(2026, 7, 15))
        self.assertEqual(t.score, ACTION_THRESHOLD)
        self.assertEqual(t.score, 40)

    def test_scores_just_under_the_action_threshold(self):
        # Average health (22) + a renewal 100 days out (8) + a one-grade dip
        # over three months (9) = 39.
        t = score(
            health_category="average",
            renewal_date=date(2026, 9, 23),
            history=["good", "average"],
        )
        self.assertEqual((date(2026, 9, 23) - TODAY).days, 100)
        self.assertEqual(t.score, 39)
        self.assertLess(t.score, ACTION_THRESHOLD)

    def test_factors_are_sorted_largest_first(self):
        t = score(
            health_category="poor",  # 66
            csm_pulse=4,
            ai_pulse=2,  # 22
            renewal_date=date(2026, 7, 15),  # 18
        )
        points = [f["points"] for f in t.factors]
        self.assertEqual(points, sorted(points, reverse=True))
        self.assertEqual(points, [66, 22, 18])

    def test_score_is_always_the_sum_of_its_factors(self):
        t = score(
            health_category="poor",
            csm_pulse=4,
            ai_pulse=1,
            renewal_date=date(2026, 7, 15),
            history=["good", "average", "poor"],
        )
        self.assertEqual(t.score, sum(f["points"] for f in t.factors))
        self.assertEqual(len(t.factors), 4)


class FrontendMirrorTests(SimpleTestCase):
    """Verbatim (argument-shape-translated) assertions from triage.test.ts,
    so the port is provably the same rule rather than one that merely agrees
    on the cases above."""

    def test_ranks_a_worse_health_status_higher(self):
        # Mirrors scoreRow's "ranks a worse health status higher".
        good = score(health_category="good").score
        average = score(health_category="average").score
        poor = score(health_category="poor").score
        self.assertGreater(poor, average)
        self.assertGreater(average, good)

    def test_only_charges_for_a_colder_ai_read_not_a_colder_csm_read(self):
        # Mirrors scoreRow's test of the same name.
        ai_colder = score(csm_pulse=5, ai_pulse=2)
        csm_colder = score(csm_pulse=2, ai_pulse=5)
        self.assertGreater(ai_colder.score, 0)
        self.assertEqual(csm_colder.score, 0)

    def test_counts_a_past_renewal_as_urgent_not_as_distant(self):
        # Mirrors the "overdue renewals" describe block's first test: an
        # overdue renewal scores the same as an equally-urgent upcoming one.
        overdue = score(renewal_date=date(2026, 1, 1))  # 165 days ago
        soon = score(renewal_date=date(2026, 7, 1))  # 16 days away
        self.assertLess(overdue.days_to_renewal, 0)
        self.assertEqual(overdue.score, soon.score)


class WeightTests(SimpleTestCase):
    def test_no_pilot_weight_is_exposed(self):
        # The frontend carries a `pilot` weight; the backend has no Pilot
        # lifecycle stage, so it's dropped entirely rather than ported unused.
        self.assertNotIn("pilot", WEIGHT)
