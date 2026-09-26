"""The story's one keyset cursor.

Items are newest first by `(occurred_at, kind, id)`, all three descending,
across every source. The cursor names the last item served; the next page is
every item that sorts strictly after it. The cut is by value, not by a row
count, so rows added or removed elsewhere never cause a skip or a repeat.

The cursor also carries a hash of the filters it was cut under, the
portfolio's rule (`shape.filter_fingerprint`): changing any filter while
keeping the cursor reads the new list from its first page. A malformed or
tampered cursor reads as absent, the first page too.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q

from .params import KINDS


@dataclass(frozen=True)
class Cut:
    at: datetime
    kind: str
    id: int


def fingerprint(params) -> str:
    """Everything that decides which items a list holds, but not `cursor`
    or `limit`. `sources` is already sorted and de-duplicated."""
    state = [params.group, list(params.sources), params.account, params.q, params.thread]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]


def encode_cursor(cut: Cut, fp: str) -> str:
    raw = json.dumps(
        {"at": cut.at.isoformat(), "k": cut.kind, "id": cut.id, "f": fp},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, fp: str) -> Cut | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict) or data.get("f") != fp:
            return None
        at, kind, ident = datetime.fromisoformat(data["at"]), data["k"], data["id"]
    except (binascii.Error, ValueError, UnicodeError, KeyError, TypeError):
        return None
    if at.tzinfo is None or kind not in KINDS or type(ident) is not int:
        return None
    return Cut(at=at, kind=kind, id=ident)


def after_q(kind: str, cut: Cut | None) -> Q:
    """Rows of `kind` after `cut`, as a filter on the `_at` annotation.

    `kind` is fixed within one source's query, so the tuple comparison
    `(_at, kind, id) < (cut.at, cut.kind, cut.id)` folds to one of three
    shapes."""
    if cut is None:
        return Q()
    if kind < cut.kind:
        return Q(_at__lte=cut.at)
    if kind == cut.kind:
        return Q(_at__lt=cut.at) | Q(_at=cut.at, id__lt=cut.id)
    return Q(_at__lt=cut.at)


def is_after(key, cut: Cut | None) -> bool:
    """`after_q` for an item already in memory: `key` is `(at, kind, id)`."""
    return cut is None or key < (cut.at, cut.kind, cut.id)
