"""Ordering, sections, totals and the keyset cursor, shared by every
portfolio list — pure functions over the entries a portfolio's book loads.
No queries.

A portfolio says only what is its own, in a `PortfolioOrder`: how an entry
reads for a sort key, its name/id tiebreak, its section for a grouping, a
section's position, and the money a section header totals. Everything else —
the rank tuple, missing values last in either direction, the section
totals, `group_value` narrowing to one Board column, and the cursor — is
here, once, so the lists cannot drift apart.

Everything runs over the whole filtered set: a section header or a tile
that counted only the page would count nothing useful.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import total_ordering

#: The bucket for "nobody" / "nothing", which always closes a by-name list.
EMPTY_KEYS = frozenset({"unassigned", "none"})


@total_ordering
class Desc:
    """Wraps an orderable value so ascending comparison sees it in reverse —
    lets one tuple comparison serve both sort directions, for any orderable
    type (numbers, dates, names), without negating anything."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == other.value

    def __lt__(self, other):
        return other.value < self.value


def wrap_desc(value, descending):
    return Desc(value) if descending else value


def name_rank(key, label):
    """A section ordered by name, the empty bucket last; the key splits two
    sections that share a name (two people called Sam). The `(int, str, str)`
    triple every section position is, so a cursor can carry it."""
    return (1 if key in EMPTY_KEYS else 0, label.casefold(), key)


def rank_of(section, value, descending, tiebreak):
    """The tuple a list sorts by and its cursor cuts at: the section's
    position (`()` ungrouped), then missing values last regardless of
    direction, then the value (reversed for descending), then the name/id
    tiebreak — always ascending, so ties keep one order either way."""
    if value is None:
        return (section, 1, None, tiebreak)
    return (section, 0, wrap_desc(value, descending), tiebreak)


@dataclass(frozen=True)
class PortfolioOrder:
    """One portfolio's own parts of its order.

    `sort_value(entry, sort_key)` is the value it sorts by, None when missing;
    `tiebreak(entry)` is `(casefolded name, id)`; `group_key(entry, group)`
    is the entry's section as `(key, label)`; `section_rank(key, label,
    group)` is that section's position as an `(int, str, str)` triple;
    `money(entry)` is what a section header totals under `money_field`, None
    when it cannot be summed (counted, not added)."""

    sort_value: Callable
    tiebreak: Callable
    group_key: Callable
    section_rank: Callable
    money: Callable
    money_field: str

    def rank(self, entry, sort_key, descending, group=""):
        section = self.section_rank(*self.group_key(entry, group), group) if group else ()
        value = self.sort_value(entry, sort_key)
        return rank_of(section, value, descending, self.tiebreak(entry))

    def order(self, entries, sort_key, descending, group=""):
        """Sections in their fixed order, then the sort inside each."""
        return sorted(entries, key=lambda entry: self.rank(entry, sort_key, descending, group))

    def groups(self, entries, group):
        """Every section present, in order, with its row count and money."""
        field = self.money_field
        groups = {}
        for entry in entries:
            key, label = self.group_key(entry, group)
            bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, field: 0.0})
            bucket["count"] += 1
            amount = self.money(entry)
            if amount is not None:
                bucket[field] += amount
        ordered = sorted(
            groups.values(),
            key=lambda bucket: self.section_rank(bucket["key"], bucket["label"], group),
        )
        for bucket in ordered:
            bucket[field] = round(bucket[field], 2)
        return ordered

    def select(self, entries, params):
        """The rows in list order — sections first, the chosen sort inside
        each — and the section totals over every row, before `group_value`
        narrows the rows to one Board column."""
        ordered = self.order(entries, params.sort_key, params.descending, params.group)
        if not params.group:
            return ordered, []
        groups = self.groups(ordered, params.group)
        if params.group_value is not None:
            ordered = [
                entry
                for entry in ordered
                if self.group_key(entry, params.group)[0] == params.group_value
            ]
        return ordered, groups

    def paginate(self, entries, params, *, fingerprint):
        """`keyset_page` over `select`'s order, cut against `fingerprint`."""
        sort_key, descending, group = params.sort_key, params.descending, params.group
        return keyset_page(
            entries,
            rank=lambda entry: self.rank(entry, sort_key, descending, group),
            cursor=params.cursor,
            limit=params.limit,
            descending=descending,
            fingerprint=fingerprint,
            grouped=bool(group),
        )


def fingerprint(state):
    """A short hash of a list's filter state — each portfolio passes every
    parameter that decides which rows the list holds and in what order, but
    never `cursor` or `limit`, with multi-value filters as sorted sets."""
    digest = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode())
    return digest.hexdigest()[:16]


def _dump_value(value):
    """A rank value, JSON-safe: dates as ISO strings, everything else as the
    JSON types it already is (a float, including `inf` for "never touched",
    or a casefolded name string)."""
    if value is None:
        return None, "none"
    if isinstance(value, date):
        return value.isoformat(), "date"
    if isinstance(value, str):
        return value, "str"
    return float(value), "num"


def _load_value(raw, kind):
    if kind == "none":
        return None
    if kind == "date":
        return date.fromisoformat(raw)
    if kind == "str":
        return raw
    if kind == "num":
        return float(raw)
    raise ValueError(kind)


def encode_cursor(section, bucket, value, name, entry_id, fingerprint):
    """The last served row's rank, unwrapped: its section's position (`[]`
    when the list is not grouped), bucket (0 present, 1 missing), its raw sort
    value, then the name/id tiebreak — exactly what `rank_of` computes, minus
    the direction wrapping, which the next request's own `descending`
    re-applies — plus the `fingerprint` of the list it was cut from."""
    dumped, kind = _dump_value(value)
    raw = json.dumps(
        {
            "sec": list(section),
            "b": bucket,
            "v": dumped,
            "t": kind,
            "n": name,
            "id": entry_id,
            "f": fingerprint,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load_section(raw):
    """A section's (position, name, key) triple, or `()` ungrouped."""
    if raw == []:
        return ()
    if (
        isinstance(raw, list)
        and len(raw) == 3
        and isinstance(raw[0], int)
        and isinstance(raw[1], str)
        and isinstance(raw[2], str)
    ):
        return tuple(raw)
    raise ValueError(raw)


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict):
            return None
        section = _load_section(data["sec"])
        bucket, kind = data["b"], data["t"]
        if bucket not in (0, 1):
            return None
        value = _load_value(data["v"], kind)
        name, entry_id, fingerprint = data["n"], data["id"], data["f"]
        if not isinstance(name, str) or not isinstance(entry_id, int):
            return None
        if not isinstance(fingerprint, str):
            return None
    except (binascii.Error, ValueError, UnicodeError, KeyError, TypeError):
        return None
    return section, bucket, value, name, entry_id, fingerprint


def keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped):
    """Keyset pagination over an already-ordered list, for any kind of entry.
    `rank(entry)` is the exact tuple the list was sorted by — `(section,
    bucket, wrapped value, (name, id))`, as `rank_of` builds it. The cursor
    names the last served row's full rank, unwrapped, plus the fingerprint of
    the list it was cut from. The next page is every entry that ranks
    strictly after it, found with a scan over `entries`. Rows added or removed
    anywhere else in the set, in any number, never cause a skip or a repeat:
    the cut is by value, not by a row count. A malformed or tampered cursor,
    or one cut from a list with another fingerprint or grouping, is treated
    as absent — the first page."""
    start = 0
    decoded = decode_cursor(cursor)
    if decoded is not None and decoded[5] == fingerprint and bool(decoded[0]) == grouped:
        section, bucket, value, name, entry_id, _fingerprint = decoded
        # Missing values are never wrapped in a rank either — only a present
        # value's direction is reversed.
        wrapped = value if bucket == 1 else wrap_desc(value, descending)
        cursor_rank = (section, bucket, wrapped, (name, entry_id))
        try:
            start = next(
                (index for index, entry in enumerate(entries) if rank(entry) > cursor_rank),
                len(entries),
            )
        except TypeError:
            # A value of a type the sort cannot compare — the safest read is
            # the first page.
            start = 0
    page = entries[start : start + limit]
    next_cursor = None
    if page and start + len(page) < len(entries):
        section, last_bucket, last_value, (last_name, last_id) = rank(page[-1])
        raw_value = last_value.value if isinstance(last_value, Desc) else last_value
        next_cursor = encode_cursor(
            section, last_bucket, raw_value, last_name, last_id, fingerprint
        )
    return page, next_cursor
