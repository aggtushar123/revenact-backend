"""Unit tier: real local embeddings (sentence-transformers), no HTTP, no
API key — free and local, so unlike anthropic_client.py these tests make
the real call rather than mocking it. Kept small/focused; retrieval.py's
own tests mock this module so the rest of the suite doesn't pay a model-
load cost for logic that doesn't actually depend on real embedding
quality."""

from django.test import SimpleTestCase

from services.copilot.embeddings import embed, rank_by_similarity


class EmbedTests(SimpleTestCase):
    def test_empty_input_returns_empty_output(self):
        self.assertEqual(embed([]), [])

    def test_returns_one_normalized_vector_per_text(self):
        vectors = embed(["Pizza Hut", "Spotify"])
        self.assertEqual(len(vectors), 2)
        for vector in vectors:
            length = sum(v * v for v in vector) ** 0.5
            self.assertAlmostEqual(length, 1.0, places=4)


class RankBySimilarityTests(SimpleTestCase):
    def test_empty_candidates_returns_empty(self):
        self.assertEqual(rank_by_similarity("anything", []), [])

    def test_real_semantic_match_beats_an_unrelated_one(self):
        # The exact case this feature exists for — a query that doesn't
        # name the company by exact string still ranks it first, purely
        # from real semantic similarity (verified live against this same
        # model before building this feature).
        ranked = rank_by_similarity(
            "that food delivery account struggling",
            ["Pizza Hut", "Spotify"],
        )
        best_index, best_score = ranked[0]
        self.assertEqual(best_index, 0)
        self.assertGreater(best_score, ranked[1][1])

    def test_results_are_sorted_best_first(self):
        ranked = rank_by_similarity("music streaming", ["Spotify", "Pizza Hut"])
        scores = [score for _, score in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))
