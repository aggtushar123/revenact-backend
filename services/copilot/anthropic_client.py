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

import time
import uuid

from django.conf import settings


class CopilotNotConfigured(Exception):
    """Raised when the selected provider's real credentials aren't set —
    SendMessageView turns this into a clear 503, never a crash or a
    silent fake answer."""


class BudgetExceeded(Exception):
    """Raised before the call is made when this organisation has spent its
    monthly token budget for the purpose — the views turn it into a 429.
    The call is logged as over_budget and never sent."""


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


def get_completion(
    system: str,
    messages: list[dict],
    *,
    max_tokens: int = 1024,
    purpose: str = "copilot",
    organisation=None,
    user=None,
) -> str:
    """`messages` is a list of `{"role": "user"|"assistant", "content": str}`
    dicts — the Anthropic Messages API's own shape (identical whether the
    real call ends up going to Anthropic directly or through Bedrock), so
    Message rows from the DB can be passed through with only a
    `.values()`-style reshape (see SendMessageView._history_for), no
    translation layer needed.

    `max_tokens` is the output budget for this call. 1024 suits a chat turn
    or a headline; a caller that asks for a list must size it to the list.

    `purpose`, `organisation` and `user` are the audit trail: every call —
    made, failed, refused — becomes one `ModelCall` row saying who asked, for
    what, and what it cost. `organisation` also selects the monthly budget
    (`ModelBudget`, else `settings.MODEL_BUDGET_DEFAULT_TOKENS`); a call that
    would start over it raises BudgetExceeded before anything is sent.
    """

    from . import usage

    provider = settings.COPILOT_LLM_PROVIDER
    started = time.monotonic()

    def log(outcome, *, model="", input_tokens=0, output_tokens=0, error=""):
        usage.record_call(
            organisation=organisation,
            user=user,
            purpose=purpose,
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome=outcome,
            error=str(error)[:500],
        )

    if organisation is not None:
        remaining = usage.remaining_tokens(organisation, purpose)
        if remaining <= 0:
            log("over_budget", error="Monthly token budget spent.")
            raise BudgetExceeded(
                f"This organisation has spent its monthly model budget for {purpose!r}. "
                "Raise it under Brain > Agents, or wait for next month."
            )

    # AI credits: one charge per call, taken before the call and given back
    # if it fails. The ledger, not this function, decides whether there is
    # anything to take; the existing BudgetExceeded handling everywhere
    # applies unchanged because the refusal is that exception.
    from services.billing import credits

    charge_reference = f"call:{uuid.uuid4()}"
    charged = False
    if organisation is not None:
        try:
            charged = credits.charge(
                organisation, purpose=purpose, reference=charge_reference, actor=user
            )
        except credits.CreditsExhausted as exc:
            log("over_budget", error=str(exc))
            raise BudgetExceeded(str(exc)) from exc

    try:
        client, model = _build_client_and_model()
    except CopilotNotConfigured as exc:
        log("unconfigured", error=exc)
        if charged:
            credits.refund(organisation, purpose=purpose, reference=charge_reference, actor=user)
        raise

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
        log("failed", model=model, error=exc)
        if charged:
            credits.refund(organisation, purpose=purpose, reference=charge_reference, actor=user)
        raise CopilotRequestFailed(str(exc)) from exc

    used = getattr(response, "usage", None)
    log(
        "ok",
        model=model,
        input_tokens=getattr(used, "input_tokens", 0) or 0,
        output_tokens=getattr(used, "output_tokens", 0) or 0,
    )
    return "".join(block.text for block in response.content if block.type == "text")
