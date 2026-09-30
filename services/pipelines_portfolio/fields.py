"""Every Opportunity or Risk field, named once: the export's columns and the
tests' proof that none is lost (tests/test_rows.py maps each model field to
one of these). MRR is in the workspace's currency, which the export prints
beside it."""

from services.organizations.fields import Field


def _organisations(row):
    return "; ".join(company["name"] for company in row["companies"])


def _account(row):
    return row["parent"]["name"] if row["parent"]["type"] == "account" else ""


def _owner(row):
    return row["owner"]["name"] if row["owner"] else "Unassigned"


def fields_for(kind):
    return (
        Field("title", "Title", lambda row: row["title"]),
        Field("revenactId", "Revenact ID", lambda row: row["id"]),
        Field("organizations", "Organizations", _organisations),
        Field("account", "Account", _account),
        Field("owner", "Owner", _owner),
        Field("stage", "Stage", lambda row: row["stage"]["label"]),
        Field("priority", "Priority", lambda row: row["priority"]["label"]),
        Field("department", "Department", lambda row: row["department"]["label"]),
        Field("mrr", "MRR", lambda row: row["mrr"]),
        Field("date", kind.date_label, lambda row: row["date"]["value"]),
        Field("stageChangedAt", "Stage Changed At", lambda row: row["stage_changed_at"]),
        Field("createdDate", "Created Date", lambda row: row["created_at"]),
    )
