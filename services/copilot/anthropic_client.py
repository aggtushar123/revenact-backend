"""The one call out to a real Claude model — the first real LLM
integration in this codebase. Pulled out into its own module the same way
services/email.py pulls each outbound-email concern into its own
function: one home for "how do we call the AI provider", easily mocked in
tests (see tests/test_views.py — SendMessageView's own tests patch
`get_completion` directly rather than making a real, paid, live call).

Two real, interchangeable providers behind the exact same `get_completion`
signature — see `settings.COPILOT_LLM_PROVIDER`: Anthropic's own API
directly (the original, still the default), or the identical Claude model
via AWS Bedrock instead (the user's own explicit choice — real AWS
credentials, a real region, and a real Bedrock model id, none of which
this module can supply or guess). Both go through the same `anthropic`
SDK (it ships an `AnthropicBedrock` client alongside the plain
`Anthropic` one) and the same `.messages.create()` call shape, so
everything past client construction is provider-agnostic.

Synchronous, single request/response, no streaming — no task queue or SSE
plumbing exists in this codebase (same limit services.scenarios.engine's
own docstring states outright for Scenario runs), so a Copilot reply is
just the return value of one blocking API call, same shape as
send_campaign_email's own single outbound call.
"""

from django.conf import settings


class CopilotNotConfigured(Exception):
    """Raised when the selected provider's real credentials aren't set —
    SendMessageView turns this into a clear 503, never a crash or a
    silent fake answer."""


class CopilotRequestFailed(Exception):
    """Raised when the real API call itself fails (bad credentials, no
    Bedrock model access granted, rate limit, network error, ...) —
    SendMessageView turns this into a clear 502 with the underlying
    message, same "log it, don't crash" spirit as CampaignSendView's own
    per-recipient try/except, just for the one external call this makes
    instead of many."""


def _build_client_and_model():
    """Real client construction for whichever provider is configured —
    raises CopilotNotConfigured with a provider-specific message when
    that provider's own real credentials aren't set, rather than a
    generic one that wouldn't tell the caller which .env vars to add."""

    import anthropic

    if settings.COPILOT_LLM_PROVIDER == "bedrock":
        if not (
            settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY and settings.AWS_REGION
        ):
            raise CopilotNotConfigured(
                "Copilot isn't configured yet — set AWS_ACCESS_KEY_ID, "
                "AWS_SECRET_ACCESS_KEY, and AWS_REGION in your .env "
                "(COPILOT_LLM_PROVIDER=bedrock)."
            )
        if not settings.BEDROCK_MODEL_ID:
            raise CopilotNotConfigured(
                "Copilot isn't configured yet — set BEDROCK_MODEL_ID in your "
                ".env to a real model id from your own AWS Bedrock console."
            )
        client = anthropic.AnthropicBedrock(
            aws_access_key=settings.AWS_ACCESS_KEY_ID,
            aws_secret_key=settings.AWS_SECRET_ACCESS_KEY,
            aws_region=settings.AWS_REGION,
        )
        return client, settings.BEDROCK_MODEL_ID

    if not settings.ANTHROPIC_API_KEY:
        raise CopilotNotConfigured(
            "Copilot isn't configured yet — set ANTHROPIC_API_KEY in your .env."
        )
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return client, settings.ANTHROPIC_MODEL


def get_completion(system: str, messages: list[dict], *, max_tokens: int = 1024) -> str:
    """`messages` is a list of `{"role": "user"|"assistant", "content": str}`
    dicts — the Anthropic Messages API's own shape (identical whether the
    real call ends up going to Anthropic directly or through Bedrock), so
    Message rows from the DB can be passed through with only a
    `.values()`-style reshape (see SendMessageView._history_for), no
    translation layer needed.

    `max_tokens` is the output budget. 1024 suits a chat turn or a headline;
    a caller that asks for a list must size it to the list — the classifier
    sends twenty records a call, and twenty pretty-printed answers do not fit
    in 1024, which cut every answer off at line ~128 and read as "the model's
    answer wasn't JSON" on batch after batch."""

    client, model = _build_client_and_model()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any
        # failure from the SDK (auth, rate limit, network, no Bedrock
        # model access granted, ...) becomes a clear 502 for the caller,
        # never a raw 500.
        raise CopilotRequestFailed(str(exc)) from exc

    return "".join(block.text for block in response.content if block.type == "text")
