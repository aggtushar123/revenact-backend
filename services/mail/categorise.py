"""What kind of mail a message is, decided once at sync.

The provider's own tab wins when it has one (Gmail sorts promotions, social
and updates itself). Two categories the tabs do not name — money and
newsletters — come from signals that are hard to fake: a subject about an
invoice or a renewal, a List-Unsubscribe header. Everything else is general.
Nothing here calls a model: a category is a filing decision, and it should
be the same one every time the same message arrives.
"""

import re

from .models import MailMessage

FINANCIAL = re.compile(
    r"\b(invoice|receipt|payment|billing|subscription|renewal|statement|payout|refund|"
    r"purchase order|quote|pricing)\b",
    re.I,
)

#: Local parts that mean "a system wrote this".
SYSTEM_SENDERS = {
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "notifications",
    "notification",
    "alerts",
    "alert",
    "mailer-daemon",
    "postmaster",
}


def categorise(message) -> str:
    """The category for a provider `Message`."""
    labels = set(message.labels or [])
    local = (message.from_address or "").split("@", 1)[0].lower()
    # The provider's own tab first: a promo about "your subscription" is
    # still a promo.
    if "promotions" in labels:
        return MailMessage.Category.PROMOTIONS
    if "social" in labels:
        return MailMessage.Category.SOCIAL
    if FINANCIAL.search(message.subject or ""):
        return MailMessage.Category.FINANCIAL
    unsubscribe = any(key.lower() == "list-unsubscribe" for key in (message.headers or {}))
    if unsubscribe and "updates" not in labels:
        return MailMessage.Category.NEWSLETTERS
    if "updates" in labels or "forums" in labels or local in SYSTEM_SENDERS:
        return MailMessage.Category.NOTIFICATIONS
    return MailMessage.Category.GENERAL


def is_archived(message) -> bool:
    """In no folder at all: the person filed it away."""
    return "archive" in set(message.labels or [])


def folder_of(message, direction) -> str:
    labels = set(message.labels or [])
    if "draft" in labels:
        return MailMessage.Folder.DRAFTS
    if "trash" in labels:
        return MailMessage.Folder.TRASH
    if "spam" in labels:
        return MailMessage.Folder.SPAM
    if "sent" in labels or direction == MailMessage.Direction.SENT:
        return MailMessage.Folder.SENT
    return MailMessage.Folder.INBOX


#: The folders whose mail is the company's business too: only these are
#: filed against a customer. Spam is never evidence, trash was thrown away,
#: and a draft has not happened yet.
FILED_FOLDERS = frozenset({MailMessage.Folder.INBOX, MailMessage.Folder.SENT})
