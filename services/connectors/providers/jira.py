"""Jira Cloud over the REST API (v3), one project or a JQL of the admin's own."""

from datetime import timedelta

from django.utils import timezone

from .base import (
    ProviderError,
    RemoteTicket,
    SetupField,
    TicketProvider,
    as_date,
    basic_auth,
    http_json,
    parse_datetime,
    tenant,
)

STATUS_CATEGORY = {"new": "open", "indeterminate": "in-progress", "done": "resolved"}
PRIORITY = {
    "highest": "critical",
    "blocker": "critical",
    "critical": "critical",
    "high": "high",
    "major": "high",
    "medium": "medium",
    "normal": "medium",
    "low": "low",
    "lowest": "low",
    "minor": "low",
    "trivial": "low",
}
FIELDS = "summary,status,priority,assignee,reporter,created,updated,resolutiondate,description"
FIRST_SYNC_DAYS = 90
PAGE = 100
MAX_PAGES = 10


def adf_text(node) -> str:
    """Flatten an Atlassian Document Format tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    text = node.get("text", "")
    children = adf_text(node.get("content", []))
    if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock", "blockquote"):
        children += "\n"
    return text + children


class JiraProvider(TicketProvider):
    key = "jira"
    label = "Jira Software"
    fields = [
        SetupField("site", "Jira site", placeholder="acme (from acme.atlassian.net)"),
        SetupField("email", "Atlassian account email"),
        SetupField("api_token", "API token", secret=True),
        SetupField("project", "Project key", placeholder="SUP", required=False),
    ]
    help = (
        "Create an API token at id.atlassian.com › Security. "
        "Leave the project blank to sync every issue you can see."
    )

    def _headers(self, creds):
        return basic_auth(creds["email"], creds["api_token"])

    def connect(self, form):
        host = tenant(form.get("site", ""), ".atlassian.net")
        email = (form.get("email") or "").strip()
        token = (form.get("api_token") or "").strip()
        project = (form.get("project") or "").strip().upper()
        if not email or not token:
            raise ProviderError("Account email and API token are required.")
        if project and not project.replace("_", "").isalnum():
            raise ProviderError("That does not look like a Jira project key.")
        creds = {"email": email, "api_token": token}
        me = http_json("GET", f"https://{host}/rest/api/3/myself", headers=self._headers(creds))
        if not me.get("accountId"):
            raise ProviderError("Jira did not recognise that email and token.")
        return {"host": host, "project": project}, creds

    def fetch_tickets(self, config, creds, cursor):
        host = config["host"]
        since = parse_datetime(cursor) or (timezone.now() - timedelta(days=FIRST_SYNC_DAYS))
        clauses = [f'updated >= "{since.strftime("%Y-%m-%d %H:%M")}"']
        if config.get("project"):
            clauses.insert(0, f'project = "{config["project"]}"')
        jql = " AND ".join(clauses) + " ORDER BY updated ASC"
        out, token, newest = [], None, since
        for _ in range(MAX_PAGES):
            body = {"jql": jql, "fields": FIELDS.split(","), "maxResults": PAGE}
            if token:
                body["nextPageToken"] = token
            page = http_json(
                "POST",
                f"https://{host}/rest/api/3/search/jql",
                headers=self._headers(creds),
                data=body,
            )
            for raw in page.get("issues", []):
                ticket = self._ticket(host, raw)
                out.append(ticket)
                updated = parse_datetime((raw.get("fields") or {}).get("updated"))
                if updated and updated > newest:
                    newest = updated
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                break
        return out, newest.isoformat(), creds

    def _ticket(self, host, raw):
        f = raw.get("fields") or {}
        category = ((f.get("status") or {}).get("statusCategory") or {}).get("key", "new")
        status = STATUS_CATEGORY.get(category, "open")
        priority_name = ((f.get("priority") or {}).get("name") or "medium").lower()
        reporter = f.get("reporter") or {}
        assignee = f.get("assignee") or {}
        return RemoteTicket(
            external_id=raw["key"],
            number=raw["key"],
            title=(f.get("summary") or "(no summary)")[:255],
            description=adf_text(f.get("description")).strip(),
            status=status,
            priority=PRIORITY.get(priority_name, "medium"),
            requester_email=(reporter.get("emailAddress") or "").lower(),
            requester_name=reporter.get("displayName") or "",
            assignee_name=assignee.get("displayName") or "",
            url=f"https://{host}/browse/{raw['key']}",
            opened_at=as_date(f.get("created")),
            resolved_at=as_date(f.get("resolutiondate")),
        )
