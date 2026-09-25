"""The Organizations table's 34 fields, named once: the export's columns and
the tests' proof that the redesign lost none of them. `id` is the frontend's
`ColumnId`; `label` is its `ALL_COLUMNS` label without the "($)" (amounts are
in the row's own currency, which the export prints beside them)."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    value: Callable[[dict], object]


def _detail(group, key):
    return lambda row: row["details"][group][key]


def _by(person_key, date_key):
    def value(row):
        history = row["details"]["history"]
        person = history[person_key]
        name = person["name"] if person else "System"
        return f"{name} / {history[date_key][:10]}"

    return value


def _products(row):
    products = row["details"]["adoption"]["products"]
    if products["primary"] is None:
        return ""
    extra = products["additional_count"]
    name = products["primary"]["name"]
    return f"{name} (+{extra})" if extra else name


FIELDS = (
    Field("organization", "Organization", lambda row: row["name"]),
    Field("revenactId", "Revenact ID", _detail("profile", "revenact_id")),
    Field("owner", "Owner", lambda row: row["owner"]["name"] if row["owner"] else "Unassigned"),
    Field("lifecycleStage", "Lifecycle Stage", lambda row: row["lifecycle"]["label"]),
    Field("health", "Health", lambda row: row["health"]["score"]),
    Field("pulse", "Pulse", lambda row: " ".join(str(p) for p in row["pulse"]["history"])),
    Field("aiPulseScore", "AI Pulse Score", lambda row: row["pulse"]["ai_label"]),
    Field("aiPulseReason", "AI Pulse Reason", _detail("voice", "ai_pulse_reason")),
    Field("nps", "NPS", _detail("voice", "nps_score")),
    Field("csatScore", "CSAT Score", _detail("voice", "csat_score")),
    Field("joinedDate", "Joined Date", _detail("contract", "joined_date")),
    Field("renewalDate", "Renewal Date", _detail("contract", "renewal_date")),
    Field(
        "arrAccount",
        "Total ARR Billed At Account",
        _detail("commercial", "arr_billed_at_account"),
    ),
    Field("arrHQ", "Total ARR Billed At HQ", _detail("commercial", "arr_billed_at_hq")),
    Field("implFee", "Implementation Fee (One Time)", _detail("commercial", "implementation_fee")),
    Field("tcv", "Total Contract Value", _detail("commercial", "total_contract_value")),
    Field(
        "tcvRenewal",
        "Total Forecasted Renewal Revenue",
        _detail("commercial", "total_forecasted_renewal_revenue"),
    ),
    Field("contractStart", "Contract Start Date", _detail("contract", "contract_start_date")),
    Field("contractEnd", "Contract End Date", _detail("contract", "contract_end_date")),
    Field("productsUtilized", "Products Utilized", _products),
    Field("topSourceChannel", "Top Source Channel", _detail("profile", "top_source_channel")),
    Field(
        "totalContractedSeats",
        "Total Contracted Seats",
        _detail("adoption", "total_contracted_seats"),
    ),
    Field("totalActiveSeats", "Total Active Seats", _detail("adoption", "total_active_seats")),
    Field(
        "totalSeatUtilization",
        "Total Seat Usage Utilization %",
        _detail("adoption", "seat_utilization_percentage"),
    ),
    Field("totalHires", "Total Hires", _detail("adoption", "total_hires")),
    Field("scopeWebApp", "Scope WebApp", _detail("adoption", "scope_web_app")),
    Field("cesPercentage", "CES Percentage", _detail("voice", "ces_percentage")),
    Field("churnDate", "Churn Date", _detail("history", "churn_date")),
    Field("churnReason", "Churn Reason", _detail("history", "churn_reason_label")),
    Field("churnComment", "Churn Comment", _detail("history", "churn_comment")),
    Field("domain", "Domain", _detail("profile", "domain")),
    Field("createdBy", "Created By / Created Date", _by("created_by", "created_at")),
    Field("modifiedBy", "Modified By / Modified Date", _by("modified_by", "updated_at")),
    Field("nameAddress", "Name / Address", _detail("profile", "address")),
)
