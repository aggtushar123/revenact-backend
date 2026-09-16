"""Slack: every top-level message in one channel is a ticket.

A shared support channel is where many teams actually take requests. A
message becomes a ticket when posted, is resolved when someone reacts
with ✅ (white_check_mark), and is filed on the customer the poster's
email belongs to (a Slack Connect guest's address is their company's).
Connect with a bot token, or through OAuth when the deployment has a
Slack app configured.
"""

from datetime import datetime, timezone
from urllib.parse import urlencode

from django.conf import settings

from .base import (
    ProviderError,
    RemoteTicket,
    SetupField,
    TicketProvider,
    first_line,
    http_json,
)

API = "https://slack.com/api"
SCOPES = ",".join(
    [
        "channels:history",
        "channels:read",
        "groups:history",
        "groups:read",
        "users:read",
        "users:read.email",
        "reactions:read",
    ]
)
DONE_REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}
URGENT_WORDS = ("urgent", "asap", "outage", "down", "p0", "p1", ":rotating_light:", ":fire:")
PAGE = 200
MAX_PAGES = 5


class SlackProvider(TicketProvider):
    key = "slack"
    label = "Slack"
    number_prefix = "SL"
    uses_oauth = True
    fields = [
        SetupField("channel", "Channel ID", placeholder="C0123ABCD (channel details › about)"),
        SetupField("bot_token", "Bot token", secret=True, placeholder="xoxb-…", required=False),
    ]
    help = (
        "Invite the bot to the channel. Leave the token blank to sign in with Slack "
        "when it is set up on this deployment."
    )

    @classmethod
    def oauth_configured(cls):
        return bool(settings.SLACK_OAUTH_CLIENT_ID and settings.SLACK_OAUTH_CLIENT_SECRET)

    @staticmethod
    def _headers(creds):
        return {"Authorization": f"Bearer {creds['bot_token']}"}

    def _call(self, creds, method, **params):
        query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        data = http_json("GET", f"{API}/{method}?{query}", headers=self._headers(creds))
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise ProviderError(
                f"Slack: {error}",
                reauth=error in ("invalid_auth", "token_revoked", "account_inactive"),
            )
        return data

    def _channel(self, form):
        channel = (form.get("channel") or "").strip().upper()
        if not channel or channel[0] not in "CG" or not channel.isalnum():
            raise ProviderError("Channel ID should look like C0123ABCD.")
        return channel

    def connect(self, form):
        channel = self._channel(form)
        token = (form.get("bot_token") or "").strip()
        if not token:
            raise ProviderError("A bot token is required unless you sign in with Slack.")
        creds = {"bot_token": token}
        self._call(creds, "auth.test")
        info = self._call(creds, "conversations.info", channel=channel)
        name = (info.get("channel") or {}).get("name", "")
        return {"channel": channel, "channel_name": name}, creds

    def authorize_url(self, state, redirect_uri):
        return "https://slack.com/oauth/v2/authorize?" + urlencode(
            {
                "client_id": settings.SLACK_OAUTH_CLIENT_ID,
                "scope": SCOPES,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )

    def exchange_code(self, code, redirect_uri, form):
        data = http_json(
            "POST",
            f"{API}/oauth.v2.access",
            form={
                "code": code,
                "client_id": settings.SLACK_OAUTH_CLIENT_ID,
                "client_secret": settings.SLACK_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
            },
        )
        if not data.get("ok") or not data.get("access_token"):
            raise ProviderError(f"Slack: {data.get('error', 'no token returned')}")
        return self.connect({"channel": form.get("channel", ""), "bot_token": data["access_token"]})

    def fetch_tickets(self, config, creds, cursor):
        channel = config["channel"]
        out, newest, page_cursor, users = [], cursor or "0", None, {}
        for _ in range(MAX_PAGES):
            page = self._call(
                creds,
                "conversations.history",
                channel=channel,
                oldest=cursor or None,
                limit=PAGE,
                cursor=page_cursor,
            )
            for raw in page.get("messages", []):
                ts = raw.get("ts", "")
                if raw.get("subtype") or (raw.get("thread_ts") and raw["thread_ts"] != ts):
                    continue
                if not raw.get("user") or not raw.get("text"):
                    continue
                if raw["user"] not in users:
                    try:
                        users[raw["user"]] = self._call(creds, "users.info", user=raw["user"]).get(
                            "user", {}
                        )
                    except ProviderError:
                        users[raw["user"]] = {}
                out.append(self._ticket(channel, raw, users[raw["user"]]))
                if ts > newest:
                    newest = ts
            page_cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not page.get("has_more") or not page_cursor:
                break
        return out, newest if newest != "0" else "", creds

    def _ticket(self, channel, raw, user):
        ts = raw["ts"]
        text = raw.get("text", "")
        profile = user.get("profile") or {}
        reactions = {r.get("name") for r in raw.get("reactions", [])}
        done = bool(reactions & DONE_REACTIONS)
        posted = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
        lowered = text.lower()
        return RemoteTicket(
            external_id=ts,
            number=f"{self.number_prefix}-{ts.replace('.', '')[-8:]}",
            title=first_line(text) or "(empty message)",
            description=text,
            status="resolved" if done else "open",
            priority="high" if any(w in lowered for w in URGENT_WORDS) else "medium",
            requester_email=(profile.get("email") or "").lower(),
            requester_name=user.get("real_name")
            or profile.get("real_name")
            or user.get("name", ""),
            url=f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}",
            opened_at=posted,
            resolved_at=datetime.now(tz=timezone.utc).date() if done else None,
        )
