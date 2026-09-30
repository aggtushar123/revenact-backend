from dataclasses import replace

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params

from .attention import build_account_attention
from .scope import resolve_account_scope


class AccountStoryView(APIView):
    """GET /api/v1/accounts/<id>/story/ — the account page's Story: every
    record filed on this account, newest first across all sources under one
    keyset cursor, filtered by group, source, search and email thread, with
    counts over the whole filtered set and the account's Needs attention
    block. Twice filtered: the account must be visible (404 otherwise,
    before anything is read), then each record is read under its own rule.
    `account` is ignored: one account has no account chips. Unknown
    parameter values are ignored. See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        scope = resolve_account_scope(request.user, pk)
        params = replace(parse_story_params(request.query_params), account=None)
        return Response(
            build_story(
                request.user,
                scope,
                params,
                today=timezone.localdate(),
                attention=build_account_attention,
            )
        )
