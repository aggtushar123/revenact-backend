"""The management brief: the metric layer, read out loud by Claude.

The Headlines generator writes one account's story from that account's
records. This writes the organisation's story from the metric layer — the
numbers, what moved since the last month-end, and what the cuts say drove
it — and stores it, so the page shows the last brief rather than paying for
a new one on every load.

**Every sentence is grounded in evidence the prompt was given.** The prompt
carries the same figures the screen shows and nothing else: no records, no
names beyond the members of a cut, no history the snapshots don't hold. The
evidence is stored beside the brief, so a reader can check any claim against
the numbers it was written from.

**Costs a real model call.** Generation is an explicit POST, never a side
effect of viewing the page.
"""

import json
from datetime import date

from django.utils import timezone

from services.copilot.anthropic_client import get_completion

from .models import Brief
from .registry import BY_KEY, DIMENSION_LABELS, METRICS, compute_all, compute_slices
from .signals import signals_for

#: Enough for a headline, four short paragraphs and a watch list, with room.
OUTPUT_TOKENS = 1400

#: The cuts worth putting in front of the model: the risk and revenue figures
#: a manager would ask "by whom?" about first.
CUT_METRICS = ("at_risk_arr", "nrr", "active_arr", "poor_health_count")
CUT_MEMBERS = 4


class NothingToBrief(Exception):
    """No customers at all — there is no organisation to write about."""


SYSTEM_PROMPT = """You are writing the monthly management brief for a B2B \
software company, from its customer-success platform's own figures.

You will be given the organisation's headline metrics as they stand today, \
each with the value recorded at the last month-end and the change since; the \
metrics that moved materially, with the members (owners, products, size bands, \
lifecycle stages) that moved them most; and a few of the figures cut by owner \
and by product.

Write a JSON object with exactly three keys:

"headline": one sentence, at most 20 words, stating the single most important \
thing in the figures. A fact, not a mood.

"body": 3-5 short paragraphs as one string separated by blank lines. Cover: the \
state of the book (revenue, retention, concentration); what moved since the \
month-end and what drove it, or that nothing moved materially; where the risk \
sits by owner and product; and what the figures cannot say (unmeasured items, \
one month of history).

"watch": an array of 2-4 strings, each one specific thing to watch or do next \
month, each citing a figure from the evidence.

Rules:
- Ground every claim in the figures given. Never invent a metric, a name, a \
date, or a dollar figure that is not present. Quote figures as given.
- "Unmeasured" means unmeasured. Never treat a missing figure as zero or as \
bad news.
- If there is no previous month-end, say so once and write about the present.
- Write plainly, no marketing language, no exclamation marks. British spelling.
"""


def _money(value, currency):
    return f"{currency} {value:,.0f}" if value is not None else "unmeasured"


def _fmt(metric, value, currency):
    if value is None:
        return "unmeasured"
    if metric.unit == "money":
        return _money(value, currency)
    if metric.unit == "percent":
        return f"{value}%"
    return f"{int(round(value))}"


def _fmt_change(metric, change, currency):
    if change is None:
        return "no comparison"
    if change == 0:
        return "unchanged"
    sign = "+" if change > 0 else ""
    if metric.unit == "percent":
        return f"{sign}{change} points"
    return f"{sign}{_fmt(metric, change, currency)}"


def build_evidence(organisation):
    """Everything the model is allowed to know, as plain data."""
    from .signals import latest_whole_org, number

    values = compute_all(organisation)
    if not values["active_customers"] and not values["churned_arr_12m"]:
        raise NothingToBrief("There are no customers to write a brief about yet.")

    latest = latest_whole_org(organisation)
    currency = organisation.currency
    metrics = []
    for metric in METRICS:
        now = number(values[metric.key])
        previous = latest.get(metric.key)
        previous_value = number(previous.value) if previous else None
        metrics.append(
            {
                "key": metric.key,
                "label": metric.label,
                "unit": metric.unit,
                "better": metric.better,
                "value": now,
                "previous": (
                    {"period_end": previous.period_end.isoformat(), "value": previous_value}
                    if previous
                    else None
                ),
                "change": (
                    round(now - previous_value, 4)
                    if now is not None and previous_value is not None
                    else None
                ),
            }
        )

    signals = signals_for(organisation, values)
    slices = compute_slices(organisation)
    cuts = []
    for key in CUT_METRICS:
        metric = BY_KEY[key]
        for dimension, members in slices.get(key, {}).items():
            ranked = sorted(
                ((m, label, number(v)) for m, label, v in members),
                key=lambda item: (item[2] is None, -(item[2] or 0)),
            )[:CUT_MEMBERS]
            cuts.append(
                {
                    "metric": key,
                    "label": metric.label,
                    "unit": metric.unit,
                    "dimension": dimension,
                    "dimension_label": DIMENSION_LABELS[dimension],
                    "members": [
                        {"member": m, "label": label, "value": v} for m, label, v in ranked
                    ],
                }
            )

    return {
        "as_of": signals["as_of"],
        "baseline": signals["baseline"],
        "currency": currency,
        "metrics": metrics,
        "signals": signals["signals"],
        "cuts": cuts,
    }


def build_prompt(evidence):
    """The evidence as lines the model reads — the same figures, no more."""
    currency = evidence["currency"]
    lines = [f"Figures as of {evidence['as_of']}. Currency: {currency}."]
    if evidence["baseline"]:
        lines.append(f"Last month-end recorded: {evidence['baseline']}.")
    else:
        lines.append("No month-end has been recorded yet, so there is nothing to compare against.")

    lines.append("\nHEADLINE METRICS (today; last month-end; change):")
    for m in evidence["metrics"]:
        metric = BY_KEY[m["key"]]
        prev = _fmt(metric, m["previous"]["value"], currency) if m["previous"] else "no month-end"
        lines.append(
            f"- {m['label']}: {_fmt(metric, m['value'], currency)}; {prev}; "
            f"{_fmt_change(metric, m['change'], currency)}"
        )

    lines.append("\nMATERIAL MOVES SINCE THE MONTH-END:")
    if not evidence["signals"]:
        lines.append("- none")
    for s in evidence["signals"]:
        metric = BY_KEY[s["key"]]
        verdict = "worse" if s["improved"] is False else "better" if s["improved"] else "changed"
        line = (
            f"- {s['label']}: {_fmt(metric, s['value'], currency)} from "
            f"{_fmt(metric, s['previous']['value'], currency)} "
            f"({_fmt_change(metric, s['change'], currency)}, {verdict})"
        )
        if s["drivers"]:
            line += "; driven by " + ", ".join(
                f"{d['label']} ({d['dimension_label'].lower()}) "
                f"{_fmt_change(metric, d['change'], currency)}"
                for d in s["drivers"]
            )
        lines.append(line)

    lines.append("\nCUTS (largest members):")
    for cut in evidence["cuts"]:
        metric = BY_KEY[cut["metric"]]
        members = ", ".join(
            f"{m['label']} {_fmt(metric, m['value'], currency)}" for m in cut["members"]
        )
        lines.append(f"- {cut['label']} by {cut['dimension_label'].lower()}: {members}")

    return "\n".join(lines)


def _parse(raw):
    """The model's JSON, tolerating a ```json fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        # strict=False: the body is paragraphs separated by blank lines, and
        # the model writes those as real newlines inside the string. Strict
        # JSON forbids that; the first live brief came back with 385
        # characters of good prose and a parse error.
        payload = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's answer wasn't JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("The model returned JSON, but not an object.")
    headline = payload.get("headline")
    body = payload.get("body")
    watch = payload.get("watch")
    if not isinstance(headline, str) or not headline.strip():
        raise ValueError("The model's answer had no headline.")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("The model's answer had no body.")
    if not isinstance(watch, list):
        watch = []
    watch = [item.strip() for item in watch if isinstance(item, str) and item.strip()][:4]
    return headline.strip(), body.strip(), watch


def generate_brief(organisation, *, generated_by=None):
    """Write and store this organisation's brief. Costs one model call.

    Lets CopilotNotConfigured / CopilotRequestFailed through untouched, the
    same way generate_headlines does, so the view can map them to 503/502.
    Raises NothingToBrief on an empty organisation and ValueError on an
    unreadable answer.
    """
    evidence = build_evidence(organisation)
    raw = get_completion(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(evidence)}],
        max_tokens=OUTPUT_TOKENS,
        purpose="brief",
        organisation=organisation,
        user=generated_by,
    )
    headline, body, watch = _parse(raw)
    # Real dates, not the ISO strings the evidence carries: the instance is
    # returned as-is, and a string would only become a date on reload.
    return Brief.objects.create(
        organisation=organisation,
        as_of=date.fromisoformat(evidence["as_of"]),
        baseline=date.fromisoformat(evidence["baseline"]) if evidence["baseline"] else None,
        headline=headline,
        body=body,
        watch=watch,
        evidence=evidence,
        generated_by=generated_by,
        generated_at=timezone.now(),
    )
