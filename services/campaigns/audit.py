"""Campaign writes, audited (SOC2:LOG-01): `campaign.created|updated|deleted|
sent`, the same shape as services/customers/pipeline_audit.py.

An event names the campaign by id and its recipients by id — never the
campaign's name, subject or body, and never a contact's name or address
(`target_repr` is just the kind and id). A PATCH that changes nothing
records nothing. Each event lands in the same transaction as its write."""

from core import audit


def record(request, campaign, verb, metadata=None):
    audit.record(  # SOC2:LOG-01
        f"campaign.{verb}",
        request=request,
        target=campaign,
        target_repr=f"campaign {campaign.pk}",
        metadata=metadata or {},
    )


def changed_fields(instance, validated_data):
    """The validated fields whose value differs from the row's, sorted."""
    return sorted(
        name for name, value in validated_data.items() if getattr(instance, name) != value
    )
