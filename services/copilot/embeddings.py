"""Real local embeddings — sentence-transformers, no API key, no
per-request cost, running entirely on this machine (the user's own
explicit choice over a paid API like Voyage AI). No pgvector either —
this Postgres instance doesn't have the `vector` extension available
(a native Homebrew install, not the pgvector-capable Docker image in
docker-compose.yml) and installing a system package on someone's own
machine isn't something to do without asking. Plain Python cosine
similarity over in-memory vectors instead — genuinely sufficient at
this system's real scale (a handful of companies, dozens of content
rows each), not the deliberate compromise a much larger corpus would
need a real ANN index for.

The model loads once, lazily, at first real use — not at Django
startup, so most Copilot sends (retrieval.py's own plain substring
match already finds the company most of the time) never pay that cost
at all."""

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        # A small, fast, well-regarded general-purpose model — chosen
        # for being good enough at this system's real scale, not
        # leaderboard SOTA.
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Normalized embeddings (unit length) for each of `texts` — with
    vectors pre-normalized, cosine similarity is just a plain dot
    product (see rank_by_similarity's own)."""

    if not texts:
        return []
    vectors = _get_model().encode(list(texts), normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]


def rank_by_similarity(query: str, candidates: list[str]) -> list[tuple[int, float]]:
    """Every index into `candidates`, paired with its real cosine
    similarity to `query`, best match first. The caller decides how
    many to keep and what similarity counts as "relevant enough" —
    this just does the real ranking."""

    if not candidates:
        return []
    query_vector = embed([query])[0]
    candidate_vectors = embed(candidates)
    scored = [
        (i, sum(q * c for q, c in zip(query_vector, vector)))
        for i, vector in enumerate(candidate_vectors)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
