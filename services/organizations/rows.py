"""One portfolio row as the page reads it: the header's eight elements plus
the opened row's six panels (`details`). Every one of the old table's 34
fields is here — `fields.FIELDS` names where."""

from services.customers.models import Customer

#: "When they differ by 2 or more, a 'pulses disagree' marker shows" (spec §1).
PULSE_DISAGREE_GAP = 2


def initials(name):
    """react-ts-app `formatters.initials`: the first letters of the first two
    words, upper-cased, or "?"."""
    letters = "".join(part[0] for part in name.split()[:2])
    return letters.upper() or "?"


def iso(value):
    return None if value is None else value.isoformat()


def number(value):
    return None if value is None else float(value)


def person(user):
    return None if user is None else {"id": user.pk, "name": user.name}


def pulse_payload(record):
    """The row's pulse, for anything that carries the two pulses, the AI
    reason and the stored history dots — a Customer or an Account."""
    csm, ai = record.csm_pulse_score, record.ai_pulse_value
    ai_category = record.ai_pulse_score
    return {
        "csm": csm,
        "ai": ai,
        "ai_category": ai_category,
        "ai_label": Customer.AIPulseScore(ai_category).label if ai_category else "",
        "reason": record.ai_pulse_reason,
        # The old table's "Pulse" column: the stored history dots.
        "history": list(record.pulse or []),
        "disagree": csm is not None and ai is not None and abs(csm - ai) >= PULSE_DISAGREE_GAP,
    }


def details_payload(customer):
    """The opened row. Money here is in the customer's own contract currency
    (`commercial.currency`), as the old table printed it; the row's `arr` is
    the converted figure."""
    product = customer.primary_product
    return {
        "commercial": {
            "currency": customer.currency,
            "arr_billed_at_account": number(customer.arr_billed_at_account),
            "arr_billed_at_hq": number(customer.arr_billed_at_hq),
            "total_contract_value": number(customer.total_contract_value),
            "total_forecasted_renewal_revenue": number(customer.total_forecasted_renewal_revenue),
            "implementation_fee": number(customer.implementation_fee),
        },
        "contract": {
            "joined_date": iso(customer.joined_date),
            "contract_start_date": iso(customer.contract_start_date),
            "renewal_date": iso(customer.renewal_date),
            "contract_end_date": iso(customer.contract_end_date),
        },
        "adoption": {
            "total_contracted_seats": customer.total_contracted_seats,
            "total_active_seats": customer.total_active_seats,
            "seat_utilization_percentage": customer.seat_utilization_percentage,
            "total_hires": customer.total_hires,
            "products": {
                "primary": None if product is None else {"id": product.pk, "name": product.name},
                "additional_count": customer.additional_products_count,
            },
            "scope_web_app": customer.scope_web_app,
        },
        "voice": {
            "nps_score": customer.nps_score,
            "csat_score": number(customer.csat_score),
            "ces_percentage": number(customer.ces_percentage),
            "ai_pulse_reason": customer.ai_pulse_reason,
        },
        "profile": {
            "revenact_id": customer.pk,
            "domain": customer.domain,
            "address": customer.address,
            "top_source_channel": customer.top_source_channel,
        },
        "history": {
            "created_by": person(customer.created_by),
            "created_at": iso(customer.created_at),
            "modified_by": person(customer.modified_by),
            "updated_at": iso(customer.updated_at),
            "churn_date": iso(customer.churn_date),
            "churn_reason": customer.churn_reason,
            "churn_reason_label": (
                customer.get_churn_reason_display() if customer.churn_reason else ""
            ),
            "churn_comment": customer.churn_comment,
        },
    }


def row_payload(entry):
    customer = entry.customer
    return {
        "id": customer.pk,
        "name": customer.name,
        "initials": initials(customer.name),
        "owner": person(customer.owner),
        "lifecycle": {
            "value": customer.lifecycle_stage,
            "label": customer.get_lifecycle_stage_display(),
        },
        "health": {
            "score": float(customer.health_score),
            "category": customer.health_category,
            "trend": entry.trend,
        },
        "renewal": {"date": iso(customer.renewal_date), "days": entry.renewal_days},
        "arr": entry.arr,
        "risk": {"score": entry.triage.score, "direction": entry.triage.direction},
        "pulse": pulse_payload(customer),
        "last_touch_days": entry.last_touch_days,
        "urgent_tickets": entry.urgent_tickets,
        "signal": entry.signal,
        "is_archived": customer.is_archived,
        "churned": entry.churned,
        "details": details_payload(customer),
    }
