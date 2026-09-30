"""One Accounts portfolio row as the page reads it: the header fields plus the
opened row's four panels (`details`). Every Account field is in one of them —
`fields.FIELDS` names where, and tests/test_rows.py pins it."""

from services.organizations.rows import initials, iso, number, person, pulse_payload


def details_payload(account, organisations):
    """The opened row. `organisations` are the linked ones the viewer may
    open, `(id, name)` pairs, lowest id first. `csm_pulse_modified_at` sits in
    History: the spec's panels do not name it, and every field must appear
    once. Profile values are the account's own; the frontend's fallback to
    the parent's contact details (see `Account`) is not applied here."""
    return {
        "commercial": {
            "arr": number(account.arr),
            "renewal_date": iso(account.renewal_date),
        },
        "voice": {
            "nps_score": account.nps_score,
            "csat_score": number(account.csat_score),
            "ai_pulse_reason": account.ai_pulse_reason,
        },
        "profile": {
            "revenact_id": account.pk,
            "domain": account.domain,
            "industry": account.industry,
            "email": account.email,
            "phone": account.phone,
            "address": account.address,
            "organisations": [{"id": pk, "name": name} for pk, name in organisations],
        },
        "history": {
            "created_at": iso(account.created_at),
            "updated_at": iso(account.updated_at),
            "pulse_recorded_on": iso(account.pulse_recorded_on),
            "csm_pulse_modified_at": iso(account.csm_pulse_modified_at),
        },
    }


def row_payload(entry):
    account = entry.account
    first = entry.organisations[0] if entry.organisations else None
    return {
        "id": account.pk,
        "name": account.name,
        "initials": initials(account.name),
        "owner": person(account.owner),
        "lifecycle": {
            "value": account.lifecycle_stage,
            "label": account.get_lifecycle_stage_display(),
        },
        "health": {
            "score": float(account.health_score),
            "category": account.health_category,
            "trend": entry.trend,
        },
        "renewal": {"date": iso(account.renewal_date), "days": entry.renewal_days},
        "arr": entry.arr,
        "risk": {"score": entry.triage.score, "direction": entry.triage.direction},
        "pulse": pulse_payload(account),
        "last_touch_days": entry.last_touch_days,
        "urgent_tickets": entry.urgent_tickets,
        "signal": entry.signal,
        # "Organisation · owner · …": the first the viewer may open, then "+N".
        "organisation": None if first is None else {"id": first[0], "name": first[1]},
        "extra_organisations": max(len(entry.organisations) - 1, 0),
        "details": details_payload(account, entry.organisations),
    }
