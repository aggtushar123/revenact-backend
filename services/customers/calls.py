"""Logging a call from the CallSense tab.

A call is a title, who hosted it, when, how long, and a summary. When the
person hands us a transcript instead of writing the summary, the model
writes it (and the call is classified for sentiment either way, so the
Account Pulse can count it). No model configured means no summary, not an
error: the call is still logged.
"""

import logging
import re

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


#: Words that say a call happened and nothing about what was said. A title
#: made only of these ("Weekly sync", "Zoom meeting", "Call") gives the
#: classifier nothing to read, so it is never sent.
GENERIC_TITLE_WORDS = frozenset(
    {
        "a",
        "and",
        "call",
        "catch",
        "catchup",
        "chat",
        "check",
        "checkin",
        "daily",
        "discussion",
        "event",
        "follow",
        "followup",
        "google",
        "huddle",
        "in",
        "intro",
        "meet",
        "meeting",
        "monthly",
        "new",
        "quick",
        "recording",
        "session",
        "standup",
        "sync",
        "teams",
        "the",
        "untitled",
        "up",
        "weekly",
        "with",
        "zoom",
    }
)


def is_generic_title(title: str) -> bool:
    """True when every word of the title is a generic one, or it has none."""
    words = re.findall(r"[a-z]+", (title or "").lower())
    return all(word in GENERIC_TITLE_WORDS for word in words)


def transcript_text_of(call) -> str:
    """The call's transcript as text: what the creating request handed over
    (a pasted transcript is never stored), else the stored transcript file.
    A missing or unreadable file reads as no transcript."""
    handed = getattr(call, "_transcript_text", None)
    if handed is not None:
        return handed.strip()
    if call.transcript_id is None:
        return ""
    from .files import read_transcript_text

    try:
        with call.transcript.file.open("rb") as handle:
            return read_transcript_text(handle).strip()
    except (OSError, ValueError):
        logger.warning("transcript of call %s could not be read", call.pk, exc_info=True)
        return ""


def call_text(call) -> str:
    """What the classifier reads for a call: the title, then the best text
    there is — the transcript, else the summary. The prompt caps every
    record's text (classification.MAX_TEXT_CHARS)."""
    body = transcript_text_of(call) or (call.summary or "").strip()
    return f"{call.title}. {body}" if body else call.title


def has_something_to_read(call) -> bool:
    return bool(
        transcript_text_of(call) or (call.summary or "").strip() or not is_generic_title(call.title)
    )


def classify_call(call):
    from .classification import classify_records

    organisation = (
        call.customer.organisation
        if call.customer_id
        else call.account.customers.select_related("organisation").first().organisation
    )
    classify_records([call], organisation=organisation, user=call.logged_by)
