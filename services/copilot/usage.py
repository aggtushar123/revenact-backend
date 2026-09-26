"""What the brain has spent, and what it may spend.

Read by `anthropic_client.get_completion` on every call and by the usage
endpoint. Budgets are per organisation per purpose per calendar month;
absent a `ModelBudget` row the default from settings applies.
"""

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import ModelBudget, ModelCall

#: Every purpose the codebase calls the model for, with the label a screen
#: shows. A purpose not listed here still logs; it just has no name.
PURPOSES = {
    "copilot": "Copilot chat",
    "dashboard": "Ask Revenact on the Dashboard",
    "organizations": "Ask Revenact on Organizations",
    "headlines": "Account headlines",
    "classification": "Interaction classifier",
    "brief": "Management brief",
    "proposals": "Ops agent proposals",
    "facilitator": "Session facilitator",
    "explain": "Metric explanations",
    "draft_reply": "Reply drafts",
    "attribute": "AI attributes",
    "feature_request": "Feature requests",
    "account_brief": "Account briefs",
    "anomaly": "Anomaly clusters",
    "translate": "Translation",
    "mcp": "Agents over MCP",
}


def month_start(today=None):
    today = today or timezone.localdate()
    return today.replace(day=1)


def budget_for(organisation, purpose):
    row = ModelBudget.objects.filter(organisation=organisation, purpose=purpose).first()
    return row.monthly_tokens if row else settings.MODEL_BUDGET_DEFAULT_TOKENS


def spent_this_month(organisation, purpose):
    totals = ModelCall.objects.filter(
        organisation=organisation,
        purpose=purpose,
        created_at__date__gte=month_start(),
    ).aggregate(inp=Sum("input_tokens"), out=Sum("output_tokens"))
    return (totals["inp"] or 0) + (totals["out"] or 0)


def remaining_tokens(organisation, purpose):
    return budget_for(organisation, purpose) - spent_this_month(organisation, purpose)


def record_call(**fields):
    """One audit row. Never raises: a failure to log must not turn a
    working model call into a broken one, so the write is best-effort."""
    try:
        return ModelCall.objects.create(**fields)
    except Exception:  # noqa: BLE001 — logging is not allowed to break the call
        return None


def summary(organisation):
    """Per purpose this month: calls, tokens, failures, budget, remaining."""
    since = month_start()
    rows = (
        ModelCall.objects.filter(organisation=organisation, created_at__date__gte=since)
        .values("purpose")
        .annotate(
            calls=Count("id"),
            ok=Count("id", filter=Q(outcome=ModelCall.Outcome.OK)),
            failed=Count("id", filter=~Q(outcome=ModelCall.Outcome.OK)),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
        )
    )
    by_purpose = {row["purpose"]: row for row in rows}
    out = []
    for purpose, label in PURPOSES.items():
        row = by_purpose.get(purpose, {})
        spent = (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
        budget = budget_for(organisation, purpose)
        out.append(
            {
                "purpose": purpose,
                "label": label,
                "calls": row.get("calls", 0),
                "ok": row.get("ok", 0),
                "failed": row.get("failed", 0),
                "input_tokens": row.get("input_tokens") or 0,
                "output_tokens": row.get("output_tokens") or 0,
                "spent": spent,
                "budget": budget,
                "remaining": max(0, budget - spent),
                "custom_budget": ModelBudget.objects.filter(
                    organisation=organisation, purpose=purpose
                ).exists(),
            }
        )
    return {"month_start": since.isoformat(), "purposes": out}
