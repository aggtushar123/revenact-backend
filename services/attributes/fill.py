"""Answering an AI attribute for one company.

The evidence is whatever the Copilot would retrieve for the prompt
(`retrieve_with_sources`), so an attribute cites the same notes, emails,
tickets and calls a chat answer would. The model is asked for JSON with a
value, its reasoning and the indices of the evidence lines it used; the
value is then coerced to the attribute's type, and anything the type
rejects becomes a `failed` row rather than a wrong value."""

import json
import re

from django.db.models import Max, Q

from core import audit
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
    get_completion,
)
from services.copilot.retrieval import retrieve_with_sources
from services.customers.models import Account, Call, Email, Ticket
from services.customers.scoping import SystemActor, visible_accounts, visible_customers

from .models import AIAttribute, AIAttributeValue

EVIDENCE = 12
YES = {"true", "yes", "y", "1"}
NO = {"false", "no", "n", "0"}


def parse_answer(raw: str) -> dict:
    """The model's text → the answer object, forgiving a ```json fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's answer wasn't JSON: {exc}") from exc
    if not isinstance(payload, dict) or "value" not in payload:
        raise ValueError("The model returned JSON without a value in it.")
    return payload


def coerce(value_type: str, value, options=None):
    """A raw answer → the typed value, or ValueError when the type rejects it."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if value_type == AIAttribute.ValueType.TEXT:
        return str(value).strip()
    if value_type == AIAttribute.ValueType.NUMBER:
        if isinstance(value, bool):
            raise ValueError(f"{value!r} is not a number")
        if isinstance(value, (int, float)):
            return value
        cleaned = re.sub(r"[^0-9.\-]", "", str(value))
        try:
            number = float(cleaned)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a number") from exc
        return int(number) if number.is_integer() else number
    if value_type == AIAttribute.ValueType.BOOLEAN:
        if isinstance(value, bool):
            return value
        word = str(value).strip().lower()
        if word in YES:
            return True
        if word in NO:
            return False
        raise ValueError(f"{value!r} is not yes or no")
    if value_type == AIAttribute.ValueType.PICKLIST:
        wanted = str(value).strip().lower()
        for option in options or []:
            if str(option).strip().lower() == wanted:
                return option
        raise ValueError(f"{value!r} is not one of {', '.join(map(str, options or []))}")
    raise ValueError(f"unknown value type {value_type!r}")


def _system_prompt(attribute: AIAttribute, company, lines: list[str]) -> str:
    kind = "account" if isinstance(company, Account) else "customer"
    shape = {
        AIAttribute.ValueType.TEXT: "a short phrase (string)",
        AIAttribute.ValueType.NUMBER: "a number (JSON number, no units)",
        AIAttribute.ValueType.BOOLEAN: "true or false (JSON boolean)",
        AIAttribute.ValueType.PICKLIST: "exactly one of: "
        + ", ".join(map(str, attribute.picklist_options)),
    }[attribute.value_type]
    evidence = "\n".join(f"[{i}] {line}" for i, line in enumerate(lines)) or "(nothing on record)"
    return (
        "You fill in one attribute of a company from the records below, and nothing else. "
        "Answer with JSON only, in this shape: "
        '{"value": <value or null>, "reasoning": "<one or two sentences>", '
        '"evidence": [<indices of the records you relied on>]}. '
        "When the records do not support an answer, set value to null and say why. "
        "Never guess.\n\n"
        f"Attribute: {attribute.name}\nQuestion: {attribute.prompt}\n"
        f"Value: {shape}\n\n"
        f"Records for the {kind} {company.name}:\n{evidence}"
    )


def _kwargs(company) -> dict:
    return {"account": company} if isinstance(company, Account) else {"customer": company}


def fill(attribute: AIAttribute, company, *, actor=None, viewer=None, request=None):
    """Ask the model once and write the row. Budget and configuration
    errors propagate (a view answers 429/503); a bad answer is a row.

    `viewer` scopes the evidence to what that person may read; None (the
    scheduled pass) is the organisation reading its own records."""
    items = retrieve_with_sources(company, EVIDENCE, query=attribute.prompt, viewer=viewer)
    raw = get_completion(
        system=_system_prompt(attribute, company, [item.line for item in items]),
        messages=[{"role": "user", "content": "Fill in the attribute."}],
        max_tokens=400,
        purpose="attribute",
        organisation=attribute.organisation,
        user=actor,
    )
    reasoning, value, sources = "", None, []
    status = AIAttributeValue.Status.FILLED
    try:
        answer = parse_answer(raw)
        reasoning = str(answer.get("reasoning") or "").strip()
        picked = [
            i for i in answer.get("evidence") or [] if isinstance(i, int) and 0 <= i < len(items)
        ]
        sources = [items[i].source for i in picked]
        value = coerce(attribute.value_type, answer.get("value"), attribute.picklist_options)
        if value is None:
            status = AIAttributeValue.Status.INSUFFICIENT
    except ValueError as exc:
        status = AIAttributeValue.Status.FAILED
        reasoning = f"The answer could not be used: {exc}"
    row = AIAttributeValue.objects.create(
        attribute=attribute,
        value=value,
        reasoning=reasoning,
        sources=sources,
        status=status,
        origin=AIAttributeValue.Origin.AI,
        set_by=actor,
        **_kwargs(company),
    )
    audit.record(
        "attribute.fill",
        request=request,
        actor=actor,
        organisation=attribute.organisation,
        target=row,
        metadata={"attribute": attribute.api_name, "company": company.name, "status": status},
    )
    return row


def companies_for(attribute: AIAttribute, viewer) -> list:
    """Every company the attribute applies to that the viewer may open."""
    companies = []
    if attribute.applies_to_customer:
        companies += list(visible_customers(viewer).filter(is_archived=False).order_by("name"))
    if attribute.applies_to_account:
        companies += list(visible_accounts(viewer).order_by("name"))
    return companies


def _last_value_at(attribute: AIAttribute, company):
    return AIAttributeValue.objects.filter(attribute=attribute, **_kwargs(company)).aggregate(
        at=Max("computed_at")
    )["at"]


def _has_activity_since(company, since) -> bool:
    if since is None:
        return True
    where = Q(**_kwargs(company)) & Q(ai_classified_at__gt=since)
    return any(model.objects.filter(where).exists() for model in (Email, Ticket, Call))


def refresh_nightly(organisation=None) -> int:
    """The scheduled pass: nightly attributes, companies with no value yet or
    with classified activity newer than their last value. Stops quietly when
    the organisation's budget runs out or no model is configured."""
    attributes = AIAttribute.objects.filter(refresh=AIAttribute.Refresh.NIGHTLY)
    if organisation is not None:
        attributes = attributes.filter(organisation=organisation)
    filled = 0
    for attribute in attributes.select_related("organisation"):
        if not attribute.organisation.ai_agent_enabled:
            continue
        actor = SystemActor(attribute.organisation)
        for company in companies_for(attribute, actor):
            if not _has_activity_since(company, _last_value_at(attribute, company)):
                continue
            try:
                fill(attribute, company)
            except (BudgetExceeded, CopilotNotConfigured):
                return filled
            except CopilotRequestFailed:
                continue
            filled += 1
    return filled
