"""Logging a call from the CallSense tab.

A call is a title, who hosted it, when, how long, and a summary. When the
person hands us a transcript instead of writing the summary, the model
writes it (and the call is classified for sentiment either way, so the
Account Pulse can count it). No model configured means no summary, not an
error: the call is still logged.
"""

import logging

from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured, get_completion

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You summarise customer call transcripts for a customer-success team. "
    "Write 3 to 6 short sentences in plain English: what the customer wanted, "
    "what was agreed, any risks or asks, and next steps. No preamble, no headings."
)
MAX_TRANSCRIPT_CHARS = 60_000


def summarise_transcript(text: str, *, organisation=None, user=None) -> str:
    """A summary of `text`, or "" when no model is available."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return get_completion(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text[:MAX_TRANSCRIPT_CHARS]}],
            max_tokens=400,
            purpose="call_summary",
            organisation=organisation,
            user=user,
        ).strip()
    except (CopilotNotConfigured, BudgetExceeded) as exc:
        logger.info("call summary skipped: %s", exc)
        return ""
    except Exception:  # noqa: BLE001 — a summary is a nicety; the call is still logged
        logger.warning("call summary failed", exc_info=True)
        return ""


def classify_call(call):
    from .classification import classify_records

    organisation = (
        call.customer.organisation
        if call.customer_id
        else call.account.customers.select_related("organisation").first().organisation
    )
    classify_records([call], organisation=organisation, user=call.logged_by)
