"""The two endpoints behind the Communications page.

Its own module rather than another thousand lines in `views.py`: this surface
reads four models through one merged list and shares none of that machinery
with the per-customer views next door.

What waiting means lives in `communications.py`; this file only turns it into
HTTP.
"""

from rest_framework import views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from . import communications

#: Same page size as DRF's default elsewhere, so the frontend's existing
#: `next`/`previous` walking works unchanged.
PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _int(raw, default, *, low, high):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _page(request, rows):
    """Slice `rows` into DRF's list shape.

    The merge happens in Python, so pagination does too. The queue is bounded
    by `communications.MAX_PER_KIND` before it gets here, which is what keeps
    that honest.
    """
    page = _int(request.query_params.get("page"), 1, low=1, high=10_000)
    size = _int(request.query_params.get("page_size"), PAGE_SIZE, low=1, high=MAX_PAGE_SIZE)

    count = len(rows)
    start = (page - 1) * size
    end = start + size

    def link(target):
        """The `next`/`previous` link for a page, keeping every other filter.

        Built with DRF's own `replace_query_param` rather than by formatting a
        string: it is what `PageNumberPagination` uses, so these links are
        escaped exactly the way the rest of the API's are.
        """
        if target < 1 or (target - 1) * size >= count:
            return None
        return replace_query_param(request.build_absolute_uri(), "page", target)

    return {
        "count": count,
        "next": link(page + 1),
        "previous": link(page - 1) if page > 1 else None,
        "results": rows[start:end],
    }


def _scope(request):
    return "team" if request.query_params.get("scope") == "team" else "mine"


def _kinds(request):
    wanted = [k for k in request.query_params.getlist("kind") if k in communications.KINDS]
    return wanted or None


class CommunicationsListView(views.APIView):
    """GET /api/v1/communications/ — the queue, or the whole stream.

    `?needs=false` switches from "who is waiting on you" to "what has happened
    recently", which is a different question over the same records. The queue
    is the default because it is the reason the page exists.

    Scoped by each record's own visibility rule (see `communications.py`), so
    there is nothing to gate here beyond being signed in.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = _scope(request)
        kinds = _kinds(request)

        if request.query_params.get("needs") == "false":
            rows = communications.everything_rows(request.user, kinds=kinds)
            payload = _page(request, rows)
            payload["truncated"] = False
            payload["mode"] = "everything"
            return Response(payload)

        querysets = communications.waiting_querysets(request.user, scope=scope, kinds=kinds)
        rows, truncated = communications.rows(querysets)

        search = (request.query_params.get("q") or "").strip().lower()
        if search:
            rows = [row for row in rows if _matches(row, search)]

        payload = _page(request, rows)
        payload["truncated"] = truncated
        payload["mode"] = "needs"
        payload["scope"] = scope
        return Response(payload)


def _matches(row, needle):
    account = (row.get("account") or {}).get("name", "")
    haystack = " ".join(
        [row.get("who", ""), row.get("subject", ""), row.get("snippet", ""), account]
    ).lower()
    return needle in haystack


class CommunicationsStatsView(views.APIView):
    """GET /api/v1/communications/stats/ — the four tile numbers.

    Shipped separately from the list so the tiles stay right while the list is
    filtered: clicking a tile narrows the queue, and the other three counts
    must not change underneath it. Same reasoning as the Contacts and Tickets
    stats endpoints.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = _scope(request)
        payload = communications.counts(request.user, scope=scope)
        payload["scope"] = scope
        payload["stale_questions"] = communications.stale_question_count(request.user, scope=scope)
        payload["has_mailbox"] = hasattr(request.user, "mailbox")
        return Response(payload)
