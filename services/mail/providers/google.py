"""Google Workspace / Gmail over OAuth 2.0 and the Gmail REST API.

Scopes: read mail, send mail, and the address of the mailbox. Refresh
tokens are long-lived; access tokens are refreshed on demand and the
refreshed set is handed back to the caller to store.
"""

import base64
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

from django.conf import settings

from .base import (
    Credentials,
    MailProvider,
    Message,
    ProviderError,
    http_json,
    parse_address,
    parse_address_list,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "openid",
        "email",
        "profile",
    ]
)


class GoogleProvider(MailProvider):
    key = "google"
    label = "Google Workspace / Gmail"

    @classmethod
    def configured(cls):
        return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)

    def authorize_url(self, state, redirect_uri):
        return (
            AUTH_URL
            + "?"
            + urlencode(
                {
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": SCOPES,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                }
            )
        )

    def exchange_code(self, code, redirect_uri):
        tokens = http_json(
            "POST",
            TOKEN_URL,
            form={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if "refresh_token" not in tokens:
            raise ProviderError(
                "Google did not return a refresh token; reconnect and grant access."
            )
        creds = Credentials(address="", data=self._with_expiry(tokens))
        profile = http_json("GET", f"{API}/profile", headers=self._auth(creds))
        creds.address = profile.get("emailAddress", "").lower()
        return creds

    # --- tokens -------------------------------------------------------------
    @staticmethod
    def _with_expiry(tokens):
        tokens = dict(tokens)
        tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
        return tokens

    def _fresh(self, creds):
        if creds.data.get("expires_at", 0) > time.time():
            return creds
        try:
            tokens = http_json(
                "POST",
                TOKEN_URL,
                form={
                    "refresh_token": creds.data["refresh_token"],
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                },
            )
        except ProviderError as exc:
            raise ProviderError(f"Google refused to refresh the token: {exc}", reauth=True) from exc
        data = {**creds.data, **self._with_expiry(tokens)}
        return Credentials(address=creds.address, display_name=creds.display_name, data=data)

    @staticmethod
    def _auth(creds):
        return {"Authorization": f"Bearer {creds.data['access_token']}"}

    # --- read ---------------------------------------------------------------
    def fetch_messages(self, creds, cursor):
        creds = self._fresh(creds)
        since = int(cursor) if cursor else int(time.time()) - 30 * 86400
        found, page = [], None
        while True:
            # Everything but chats: spam, trash and drafts are folders the
            # person's own inbox view shows, so they come along, labelled.
            params = {
                "q": f"after:{since} -in:chats",
                "maxResults": 100,
                "includeSpamTrash": "true",
            }
            if page:
                params["pageToken"] = page
            listing = http_json(
                "GET", f"{API}/messages?{urlencode(params)}", headers=self._auth(creds)
            )
            for stub in listing.get("messages", []):
                full = http_json(
                    "GET", f"{API}/messages/{stub['id']}?format=full", headers=self._auth(creds)
                )
                found.append(parse_gmail_message(full))
            page = listing.get("nextPageToken")
            if not page or len(found) >= 500:
                break
        newest = max((m.date for m in found), default=None)
        new_cursor = str(int(newest.timestamp()) + 1) if newest else str(since)
        return found, new_cursor, creds

    # --- send ---------------------------------------------------------------
    def send(self, creds, *, to, subject, body):
        creds = self._fresh(creds)
        mime = EmailMessage()
        mime["To"] = ", ".join(to)
        mime["From"] = creds.address
        mime["Subject"] = subject
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = http_json(
            "POST", f"{API}/messages/send", headers=self._auth(creds), data={"raw": raw}
        )
        return Message(
            provider_id=sent.get("id", ""),
            thread_id=sent.get("threadId", ""),
            subject=subject,
            from_address=creds.address,
            from_name=creds.display_name,
            to=[("", address.lower()) for address in to],
            date=datetime.now(timezone.utc),
            body=body,
        )


def _header(payload, name):
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _text_of(part):
    """The first text/plain body in a MIME tree, else text/html, else ''."""
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "==").decode(errors="replace")
    html = ""
    for child in part.get("parts", []):
        text = _text_of(child)
        if text and child.get("mimeType") == "text/plain":
            return text
        if text and not html:
            html = text
    if mime == "text/html" and data:
        return _strip_html(base64.urlsafe_b64decode(data + "==").decode(errors="replace"))
    return html


def _strip_html(html):
    import re

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


FOLDER_LABELS = frozenset({"inbox", "sent", "draft", "spam", "trash"})

#: Gmail's label ids, in the store's vocabulary.
GMAIL_LABELS = {
    "INBOX": "inbox",
    "SENT": "sent",
    "DRAFT": "draft",
    "SPAM": "spam",
    "TRASH": "trash",
    "UNREAD": "unread",
    "STARRED": "starred",
    "IMPORTANT": "important",
    "CATEGORY_PROMOTIONS": "promotions",
    "CATEGORY_SOCIAL": "social",
    "CATEGORY_UPDATES": "updates",
    "CATEGORY_FORUMS": "forums",
    "CATEGORY_PERSONAL": "personal",
}


def parse_gmail_message(full) -> Message:
    payload = full.get("payload", {})
    from_name, from_address = parse_address(_header(payload, "From"))
    date = datetime.fromtimestamp(int(full.get("internalDate", "0")) / 1000, tz=timezone.utc)
    headers = {}
    unsubscribe = _header(payload, "List-Unsubscribe")
    if unsubscribe:
        headers["list-unsubscribe"] = unsubscribe
    labels = [GMAIL_LABELS[label] for label in full.get("labelIds", []) if label in GMAIL_LABELS]
    # Gmail's archive is the absence of a folder label: the message exists,
    # and it is in no folder we know. Only when Gmail spoke at all.
    if full.get("labelIds") is not None and not FOLDER_LABELS & set(labels):
        labels.append("archive")
    return Message(
        provider_id=full.get("id", ""),
        thread_id=full.get("threadId", ""),
        subject=_header(payload, "Subject") or "(no subject)",
        from_address=from_address,
        from_name=from_name,
        to=parse_address_list(_header(payload, "To")) + parse_address_list(_header(payload, "Cc")),
        date=date,
        body=_text_of(payload)[:20000],
        headers=headers,
        labels=labels,
    )
