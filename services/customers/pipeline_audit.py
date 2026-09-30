"""Opportunity and risk writes, audited (SOC2:LOG-01). Every create, edit (a
Board drag included) and delete made through the pipeline endpoints goes
through here: `opportunity.created|updated|deleted`, `risk.…`.

An event names the record by id, its parent by id, and for an edit the
fields that changed — never their values, and never the title (a record's
`str()` quotes it, so `target_repr` is just the kind and id). A PATCH that
changes nothing records nothing. Bulk edits are one batch event of their
own (`pipelines.bulk_updated`, services/pipelines_portfolio/views.py)."""

from django.db import transaction

from core import audit


def _record(request, instance, verb, metadata):
    name = instance._meta.model_name
    audit.record(  # SOC2:LOG-01
        f"{name}.{verb}",
        request=request,
        target=instance,
        target_repr=f"{name} {instance.pk}",
        metadata=metadata,
    )


def _parents(instance):
    return {"customer_id": instance.customer_id, "account_id": instance.account_id}


def changed_fields(instance, validated_data):
    """The validated fields whose value differs from the row's, sorted."""
    return sorted(
        name for name, value in validated_data.items() if getattr(instance, name) != value
    )


def create(request, serializer, **save_kwargs):
    # One transaction: the event and the create land together or not at all.
    with transaction.atomic():
        instance = serializer.save(**save_kwargs)
        _record(request, instance, "created", _parents(instance))
    return instance


def update(request, serializer):
    changed = changed_fields(serializer.instance, serializer.validated_data)
    with transaction.atomic():
        instance = serializer.save()
        if changed:
            _record(request, instance, "updated", {"fields": changed})
    return instance


def delete(request, instance):
    # One transaction: the event and the delete land together or not at all.
    with transaction.atomic():
        _record(request, instance, "deleted", _parents(instance))
        instance.delete()
