"""Microsoft 365 / Outlook over OAuth 2.0 and Microsoft Graph."""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from django.conf import settings

from .base import Credentials, MailProvider, Message, ProviderError, http_json

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.Read Mail.Send"


def _login(path):
    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{path}"


class MicrosoftProvider(MailProvider):
    key = "microsoft"
    label = "Microsoft 365 / Outlook"

    @classmethod
    def configured(cls):
        return bool(settings.MICROSOFT_OAUTH_CLIENT_ID and settings.MICROSOFT_OAUTH_CLIENT_SECRET)

    def authorize_url(self, state, redirect_uri):
        return (
            _login("authorize")
            + "?"
            + urlencode(
                {
                    "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "response_mode": "query",
                    "scope": SCOPES,
                    "state": state,
                }
            )
        )

    def exchange_code(self, code, redirect_uri):
        tokens = http_json(
            "POST",
            _login("token"),
            form={
                "code": code,
                "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": SCOPES,
            },
        )
        if "refresh_token" not in tokens:
            raise ProviderError(
                "Microsoft did not return a refresh token; reconnect and grant access."
            )
        creds = Credentials(address="", data=self._with_expiry(tokens))
        me = http_json("GET", f"{GRAPH}/me", headers=self._auth(creds))
        creds.address = (me.get("mail") or me.get("userPrincipalName") or "").lower()
        creds.display_name = me.get("displayName", "")
        return creds

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
                _login("token"),
                form={
                    "refresh_token": creds.data["refresh_token"],
                    "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                    "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "scope": SCOPES,
                },
            )
        except ProviderError as exc:
            raise ProviderError(
                f"Microsoft refused to refresh the token: {exc}", reauth=True
            ) from exc
        return Credentials(
            address=creds.address,
            display_name=creds.display_name,
            data={**creds.data, **self._with_expiry(tokens)},
        )

    @staticmethod
    def _auth(creds):
        return {"Authorization": f"Bearer {creds.data['access_token']}"}

    def fetch_messages(self, creds, cursor):
        creds = self._fresh(creds)
        if cursor:
            since = cursor
        else:
            start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30)
            since = start.isoformat().replace("+00:00", "Z")
        params = {
            "$filter": f"lastModifiedDateTime ge {since}",
            "$orderby": "lastModifiedDateTime asc",
            "$top": "50",
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "sentDateTime",
                    "lastModifiedDateTime",
                    "body",
                    "bodyPreview",
                    "isDraft",
                    "isRead",
                    "flag",
                    "importance",
                    "parentFolderId",
                ]
            ),
        }
        folders = self._folder_ids(creds)
        url = f"{GRAPH}/me/messages?{urlencode(params)}"
        found, newest = [], since
        while url and len(found) < 500:
            page = http_json("GET", url, headers=self._auth(creds))
            for item in page.get("value", []):
                found.append(parse_graph_message(item, folders))
                newest = max(newest, item.get("lastModifiedDateTime", since))
            url = page.get("@odata.nextLink")
        return found, newest, creds

    def _folder_ids(self, creds):
        """Graph names folders by opaque id; the well-known ones resolve to
        one each. A folder that cannot be resolved simply is not labelled."""
        folders = {}
        for known, label in WELL_KNOWN_FOLDERS.items():
            try:
                item = http_json(
                    "GET", f"{GRAPH}/me/mailFolders/{known}?$select=id", headers=self._auth(creds)
                )
            except ProviderError:
                continue
            if item.get("id"):
                folders[item["id"]] = label
        return folders

    def send(self, creds, *, to, subject, body):
        creds = self._fresh(creds)
        http_json(
            "POST",
            f"{GRAPH}/me/sendMail",
            headers=self._auth(creds),
            data={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                },
                "saveToSentItems": True,
            },
        )
        return Message(
            provider_id="",
            thread_id="",
            subject=subject,
            from_address=creds.address,
            from_name=creds.display_name,
            to=[("", a.lower()) for a in to],
            date=datetime.now(timezone.utc),
            body=body,
        )


def _recipient(entry):
    address = entry.get("emailAddress", {})
    return (address.get("name", ""), address.get("address", "").lower())


#: Graph's well-known folder names, in the store's vocabulary.
WELL_KNOWN_FOLDERS = {
    "inbox": "inbox",
    "sentitems": "sent",
    "drafts": "draft",
    "junkemail": "spam",
    "deleteditems": "trash",
}


def parse_graph_message(item, folders=None) -> Message:
    sender = item.get("from", {}).get("emailAddress", {})
    labels = []
    folder = (folders or {}).get(item.get("parentFolderId", ""))
    if item.get("isDraft"):
        labels.append("draft")
    elif folder:
        labels.append(folder)
    if item.get("isRead") is False:
        labels.append("unread")
    if (item.get("flag") or {}).get("flagStatus") == "flagged":
        labels.append("starred")
    if item.get("importance") == "high":
        labels.append("important")
    body = item.get("body", {})
    text = body.get("content", "") if body.get("contentType", "").lower() == "text" else ""
    if not text:
        from .google import _strip_html

        text = _strip_html(body.get("content", "")) or item.get("bodyPreview", "")
    when = item.get("sentDateTime") or item.get("receivedDateTime") or "1970-01-01T00:00:00Z"
    return Message(
        provider_id=item.get("id", ""),
        thread_id=item.get("conversationId", ""),
        subject=item.get("subject") or "(no subject)",
        from_address=sender.get("address", "").lower(),
        from_name=sender.get("name", ""),
        to=[_recipient(r) for r in item.get("toRecipients", []) + item.get("ccRecipients", [])],
        date=datetime.fromisoformat(when.replace("Z", "+00:00")),
        body=text[:20000],
        labels=labels,
    )
