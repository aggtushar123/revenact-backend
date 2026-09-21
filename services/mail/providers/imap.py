"""Any mailbox that speaks IMAP and SMTP — the provider for everything
that is not Google or Microsoft, and for those two where a company would
rather hand out app passwords than register an OAuth client."""

import email
import imaplib
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from .base import (
    Credentials,
    MailProvider,
    Message,
    ProviderError,
    parse_address,
    parse_address_list,
)

FOLDERS = ("INBOX", "Sent", "[Gmail]/Sent Mail", "Sent Items", "Sent Messages")


class ImapProvider(MailProvider):
    key = "imap"
    label = "IMAP / SMTP"
    uses_oauth = False

    def connect_with_password(self, form):
        required = ("address", "password", "imap_host", "smtp_host")
        missing = [k for k in required if not str(form.get(k, "")).strip()]
        if missing:
            raise ProviderError(f"Missing: {', '.join(missing)}.")
        data = {
            "username": str(form.get("username") or form["address"]).strip(),
            "password": str(form["password"]),
            "imap_host": str(form["imap_host"]).strip(),
            "imap_port": int(form.get("imap_port") or 993),
            "smtp_host": str(form["smtp_host"]).strip(),
            "smtp_port": int(form.get("smtp_port") or 587),
        }
        creds = Credentials(
            address=str(form["address"]).strip().lower(),
            display_name=str(form.get("display_name") or ""),
            data=data,
        )
        with self._imap(creds):
            pass  # a login that fails raises
        return creds

    def _imap(self, creds):
        try:
            client = imaplib.IMAP4_SSL(creds.data["imap_host"], creds.data["imap_port"], timeout=30)
            client.login(creds.data["username"], creds.data["password"])
            return client
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ProviderError(f"IMAP login failed: {exc}", reauth=True) from exc

    def fetch_messages(self, creds, cursor):
        since_date = (
            datetime.fromisoformat(cursor)
            if cursor
            else datetime.now(timezone.utc) - timedelta(days=30)
        )
        client = self._imap(creds)
        found = []
        try:
            for folder in FOLDERS:
                status, _ = client.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                status, ids = client.search(None, "SINCE", since_date.strftime("%d-%b-%Y"))
                if status != "OK" or not ids or not ids[0]:
                    continue
                for uid in ids[0].split()[-300:]:
                    status, parts = client.fetch(uid, "(FLAGS RFC822)")
                    if status != "OK" or not parts or not isinstance(parts[0], tuple):
                        continue
                    message = parse_rfc822(parts[0][1], fallback_id=f"{folder}:{uid.decode()}")
                    message.labels = _labels(folder, parts)
                    if message.date >= since_date:
                        found.append(message)
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - best effort on the way out
                pass
        newest = max((m.date for m in found), default=since_date)
        return found, (newest + timedelta(seconds=1)).isoformat(), creds

    def send(self, creds, *, to, subject, body):
        mime = EmailMessage()
        mime["To"] = ", ".join(to)
        mime["From"] = creds.address
        mime["Subject"] = subject
        mime.set_content(body)
        try:
            with smtplib.SMTP(creds.data["smtp_host"], creds.data["smtp_port"], timeout=30) as smtp:
                smtp.starttls()
                smtp.login(creds.data["username"], creds.data["password"])
                smtp.send_message(mime)
        except (smtplib.SMTPException, OSError) as exc:
            raise ProviderError(f"SMTP send failed: {exc}") from exc
        return Message(
            provider_id=mime["Message-ID"] or "",
            thread_id="",
            subject=subject,
            from_address=creds.address,
            from_name=creds.display_name,
            to=[("", a.lower()) for a in to],
            date=datetime.now(timezone.utc),
            body=body,
        )


def _labels(folder, parts):
    """Where an IMAP message sits and how it was left, from the folder it
    came out of and the FLAGS in the fetch response. RFC 3501 lets a server
    put FLAGS before or after the RFC822 literal, so every envelope piece of
    the response is read, not only the first."""
    name = folder.lower()
    labels = ["sent" if "sent" in name else "inbox"]
    pieces = []
    for part in parts:
        raw = part[0] if isinstance(part, tuple) else part
        if isinstance(raw, bytes):
            pieces.append(raw.decode(errors="replace"))
        elif isinstance(raw, str):
            pieces.append(raw)
    flags = " ".join(pieces)
    if "\\Seen" not in flags:
        labels.append("unread")
    if "\\Flagged" in flags:
        labels.append("starred")
    return labels


def _decoded(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001 - malformed header
        return value or ""


def parse_rfc822(raw: bytes, fallback_id: str = "") -> Message:
    parsed = email.message_from_bytes(raw)
    body = ""
    if parsed.is_multipart():
        html = ""
        for part in parsed.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                body = text
                break
            if ctype == "text/html" and not html:
                html = text
        if not body and html:
            from .google import _strip_html

            body = _strip_html(html)
    else:
        payload = parsed.get_payload(decode=True) or b""
        body = payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
        if parsed.get_content_type() == "text/html":
            from .google import _strip_html

            body = _strip_html(body)
    try:
        date = parsedate_to_datetime(parsed.get("Date"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        date = datetime.now(timezone.utc)
    from_name, from_address = parse_address(_decoded(parsed.get("From")))
    headers = {}
    if parsed.get("List-Unsubscribe"):
        headers["list-unsubscribe"] = str(parsed.get("List-Unsubscribe"))
    return Message(
        provider_id=(parsed.get("Message-ID") or fallback_id).strip(),
        thread_id=(
            parsed.get("In-Reply-To") or parsed.get("References") or parsed.get("Message-ID") or ""
        ).split()[0]
        if (parsed.get("In-Reply-To") or parsed.get("References") or parsed.get("Message-ID"))
        else "",
        subject=_decoded(parsed.get("Subject")) or "(no subject)",
        from_address=from_address,
        from_name=from_name,
        to=parse_address_list(_decoded(parsed.get("To")))
        + parse_address_list(_decoded(parsed.get("Cc"))),
        date=date,
        body=body[:20000],
        headers=headers,
    )
