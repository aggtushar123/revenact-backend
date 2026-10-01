"""One Pipelines row as the page reads it, the list item and the Board card
alike: the title, "Part of" its organisation or account, MRR, stage,
priority, department, the date line and at most one signal. Every model
field is in the row, and in the export (`fields.fields_for`);
tests/test_rows.py pins both."""

from services.customers.serializers import department_label
from services.organizations.rows import iso, person

__all__ = ["department_label", "row_payload"]


def row_payload(entry, kind):
    item = entry.item
    return {
        "id": item.pk,
        "kind": kind.item,
        "title": item.title,
        "parent": entry.parent,
        "companies": [{"id": pk, "name": name} for pk, name in entry.organisations],
        "owner": person(entry.owner),
        "mrr": entry.mrr,
        "stage": {"value": item.stage, "label": item.get_stage_display()},
        "priority": {"value": item.priority, "label": item.get_priority_display()},
        "department": {"value": item.department, "label": department_label(item.department)},
        "date": {"value": iso(entry.when), "days": entry.days},
        "open": entry.is_open,
        "overdue": entry.overdue,
        "signal": entry.signal,
        "stage_changed_at": iso(item.stage_changed_at),
        "created_at": iso(item.created_at),
    }
