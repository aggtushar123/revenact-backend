"""Every Account field, named once: the export's columns and the tests' proof
that the portfolio lost none of them (tests/test_rows.py maps each model field
to one of these). `id` is the column's stable key; `label` its CSV header.
ARR is in the workspace's currency, which the export prints beside it."""

from services.organizations.fields import Field


def _detail(group, key):
    return lambda row: row["details"][group][key]


def _organisations(row):
    return "; ".join(item["name"] for item in row["details"]["profile"]["organisations"])


FIELDS = (
    Field("account", "Account", lambda row: row["name"]),
    Field("revenactId", "Revenact ID", _detail("profile", "revenact_id")),
    Field("organizations", "Organizations", _organisations),
    Field("owner", "Owner", lambda row: row["owner"]["name"] if row["owner"] else "Unassigned"),
    Field("lifecycleStage", "Lifecycle Stage", lambda row: row["lifecycle"]["label"]),
    Field("health", "Health", lambda row: row["health"]["score"]),
    Field("pulse", "Pulse", lambda row: " ".join(str(p) for p in row["pulse"]["history"])),
    Field("aiPulseScore", "AI Pulse Score", lambda row: row["pulse"]["ai_label"]),
    Field("aiPulseValue", "AI Pulse Value", lambda row: row["pulse"]["ai"]),
    Field("csmPulseScore", "CSM Pulse Score", lambda row: row["pulse"]["csm"]),
    Field(
        "csmPulseModifiedAt",
        "CSM Pulse Modified At",
        _detail("history", "csm_pulse_modified_at"),
    ),
    Field("aiPulseReason", "AI Pulse Reason", _detail("voice", "ai_pulse_reason")),
    Field("nps", "NPS", _detail("voice", "nps_score")),
    Field("csatScore", "CSAT Score", _detail("voice", "csat_score")),
    Field("renewalDate", "Renewal Date", _detail("commercial", "renewal_date")),
    Field("arr", "ARR", _detail("commercial", "arr")),
    Field("domain", "Domain", _detail("profile", "domain")),
    Field("industry", "Industry", _detail("profile", "industry")),
    Field("email", "Email", _detail("profile", "email")),
    Field("phone", "Phone", _detail("profile", "phone")),
    Field("address", "Address", _detail("profile", "address")),
    Field("createdDate", "Created Date", _detail("history", "created_at")),
    Field("modifiedDate", "Modified Date", _detail("history", "updated_at")),
    Field("pulseRecordedOn", "Pulse Recorded On", _detail("history", "pulse_recorded_on")),
)
