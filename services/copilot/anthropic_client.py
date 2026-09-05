"""The one call out to Anthropic's Claude API — the first real LLM
integration in this codebase. Pulled out into its own module the same way
services/email.py pulls each outbound-email concern into its own
function: one home for "how do we call the AI provider", easily mocked in
tests (see tests/test_views.py — SendMessageView's own tests patch
`get_completion` directly rather than making a real, paid, live call).

Synchronous, single request/response, no streaming — no task queue or SSE
plumbing exists in this codebase (same limit services.scenarios.engine's
own docstring states outright for Scenario runs), so a Copilot reply is
just the return value of one blocking API call, same shape as
send_campaign_email's own single outbound call.
"""

from django.conf import settings


class CopilotNotConfigured(Exception):
    """Raised when ANTHROPIC_API_KEY isn't set — SendMessageView turns
    this into a clear 503, never a crash or a silent fake answer."""


class CopilotRequestFailed(Exception):
    """Raised when the Anthropic API call itself fails (bad key, rate
    limit, network error, ...) — SendMessageView turns this into a clear
    502 with the underlying message, same "log it, don't crash" spirit
    as CampaignSendView's own per-recipient try/except, just for the one
    external call this makes instead of many."""


def get_completion(system: str, messages: list[dict]) -> str:
    """`messages` is a list of `{"role": "user"|"assistant", "content": str}`
    dicts — the Anthropic Messages API's own shape, so Message rows from
    the DB can be passed through with only a `.values()`-style reshape
    (see SendMessageView._history_for), no translation layer needed."""

    if not settings.ANTHROPIC_API_KEY:
        raise CopilotNotConfigured(
            "Copilot isn't configured yet — set ANTHROPIC_API_KEY in your .env."
        )

    import anthropic

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any
        # failure from the SDK (auth, rate limit, network, ...) becomes a
        # clear 502 for the caller, never a raw 500.
        raise CopilotRequestFailed(str(exc)) from exc

    return "".join(block.text for block in response.content if block.type == "text")
