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
    (a pasted transcript is never stored), else the stored transcript file,
    read and decoded at most once — the result is cached back onto
    `_transcript_text`, the same attribute a handed-over transcript uses, so
    `has_something_to_read` and `call_text` (both call this) share one read
    of the file instead of paying for it twice. A missing or unreadable file
    reads as no transcript: any failure a storage backend can raise — not
    just OSError/ValueError — must not fail a classification batch."""
    handed = getattr(call, "_transcript_text", None)
    if handed is not None:
        return handed.strip()
    if call.transcript_id is None:
        return ""
    from .files import read_transcript_text

    try:
        with call.transcript.file.open("rb") as handle:
            text = read_transcript_text(handle).strip()
    except Exception:  # noqa: BLE001 — a storage error is no different from a missing file
        logger.warning("transcript of call %s could not be read", call.pk, exc_info=True)
        text = ""
    call._transcript_text = text
    return text


def call_text(call) -> str:
    """What the classifier reads for a call: the title, then the best text
    there is — the summary (a digest of the whole transcript, where the
    transcript's first characters are often small talk), else the
    transcript, else nothing. The transcript is not read when there is a
    summary. The prompt caps every record's text
    (classification.MAX_TEXT_CHARS)."""
    body = (call.summary or "").strip() or transcript_text_of(call)
    return f"{call.title}. {body}" if body else call.title


def has_something_to_read(call) -> bool:
    return bool(
        (call.summary or "").strip() or transcript_text_of(call) or not is_generic_title(call.title)
    )


def organisation_of_call(call):
    if call.customer_id:
        return call.customer.organisation
    customer = call.account.customers.select_related("organisation").first()
    return customer.organisation if customer is not None else None


def classify_call(call, *, transcript_text=None, user=None):
    """The one thing every path that creates a Call runs, once the call and
    its participants are saved: read it now, so its sentiment is on the
    record and on its participants before anyone looks.

    A call with nothing to read is marked not analysable without a model
    call. Either way the people on it are recomputed exactly once. Anything
    that goes wrong is logged and swallowed: classifying is never a reason
    for a call not to be saved, and `classify_interactions` picks up
    whatever was left pending.

    Not wrapped in one outer transaction: `classify_records` makes a real,
    paid model call, and `credits.charge` holds a row lock on the
    organisation's billing account for it — the same lock every other AI
    call in the workspace needs, so it must not be held any longer than the
    request itself takes. Worse, a failure in anything after the model call
    (`recompute_for_records`, say) would roll back the credit charge and the
    usage row along with it, so the organisation is never billed for a call
    that really happened. Only the writes that never touch the network — the
    "nothing to read" branch — get their own, small atomic block."""
    from django.db import transaction

    from .classification import classify_records, mark_not_analysable
    from .contact_sentiment import recompute_for_records

    if transcript_text is not None:
        call._transcript_text = transcript_text
    try:
        if not has_something_to_read(call):
            with transaction.atomic():
                mark_not_analysable(call)
                recompute_for_records([call])
            return
        classify_records(
            [call], organisation=organisation_of_call(call), user=user or call.logged_by
        )
    except Exception:  # noqa: BLE001 — the call is saved whatever happens here
        logger.warning("classifying call %s failed", call.pk, exc_info=True)
    if call.ai_classified_at is None:
        # Not read (no model, no budget, a failed batch): being on the call
        # is still contact, so the people on it are recomputed here, once.
        # A read or marked call already recomputed them.
        try:
            recompute_for_records([call])
        except Exception:  # noqa: BLE001 — as above
            logger.warning("recomputing the people on call %s failed", call.pk, exc_info=True)
