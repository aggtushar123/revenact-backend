"""How this product cuts customers by size.

One definition, because two screens disagreeing about what a "mid-size
account" is would be worse than either of them not offering the cut at all.
The AI Trending Topics filter and the Customer Overview's composition chart
both read this.

Sized to the book this product actually holds — mid-market ARR, tens of
thousands to low hundreds. Wider bands reading "$250K-$1M" and "$1M+" would
look more impressive and be permanently empty, and a segment nobody falls into
teaches nothing.
"""

#: `(value, label, floor, ceiling_exclusive_or_None)`, smallest first.
REVENUE_BRACKETS = (
    ("under_25k", "Under $25K", 0, 25_000),
    ("25k_50k", "$25K – $50K", 25_000, 50_000),
    ("50k_100k", "$50K – $100K", 50_000, 100_000),
    ("over_100k", "$100K and above", 100_000, None),
)


def bracket_for(arr):
    """The band an ARR falls into, or None when there is no figure to place.

    Null ARR means the contract currency had no exchange rate — not that the
    customer is small, so it is left unplaced rather than dropped into the
    bottom band.
    """
    if arr is None:
        return None
    for value, _label, floor, ceiling in REVENUE_BRACKETS:
        if arr >= floor and (ceiling is None or arr < ceiling):
            return value
    return REVENUE_BRACKETS[-1][0]
