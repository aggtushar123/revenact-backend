"""Unit tier: grouping and matching on plain vectors, no DB, no model."""

from django.test import SimpleTestCase

from services.requests.gather import group_new, match_existing, parse_title


class Grouping(SimpleTestCase):
    def test_similar_vectors_share_a_group_and_others_stay_apart(self):
        vectors = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0], [0.1, 0.99]]
        self.assertEqual(group_new(vectors, threshold=0.9), [[0, 1], [2, 3]])

    def test_a_lone_vector_is_its_own_group(self):
        self.assertEqual(group_new([[1.0, 0.0]], threshold=0.9), [[0]])
        self.assertEqual(group_new([], threshold=0.9), [])


class Matching(SimpleTestCase):
    def test_returns_the_best_request_above_the_threshold(self):
        requests = {7: [1.0, 0.0], 8: [0.0, 1.0]}
        self.assertEqual(match_existing([0.95, 0.31], requests, threshold=0.9), 7)
        self.assertIsNone(match_existing([0.7, 0.7], requests, threshold=0.9))
        self.assertIsNone(match_existing([1.0, 0.0], {}, threshold=0.9))


class Title(SimpleTestCase):
    def test_reads_title_and_summary(self):
        self.assertEqual(
            parse_title(
                '```json\n{"title": "Slack alerts", "summary": "Push alerts to Slack."}\n```'
            ),
            ("Slack alerts", "Push alerts to Slack."),
        )
        with self.assertRaises(ValueError):
            parse_title("not json")
