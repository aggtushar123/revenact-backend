"""The feedback log: people correcting the system.

Every writer here records what the system said and what the person said,
in the same row. That is what makes the log worth keeping: it is the set of
cases the next prompt, taxonomy or rubric change should be read against, and
a cheap answer to "how often is the model wrong, and about what".
"""

from django.utils import timezone

from services.customers import classification, taxonomy
from services.customers.models import Call, Email, Ticket

from .models import Feedback

#: The three models that carry the AI taxonomy, by the name a URL uses.
INTERACTION_MODELS = {"ticket": Ticket, "email": Email, "call": Call}


class InvalidCorrection(ValueError):
    """The correction names a value the taxonomy doesn't have."""


def record(
    organisation,
    kind,
    *,
    subject_type,
    subject_id,
    subject_label,
    before,
    after,
    note="",
    made_by=None,
):
    return Feedback.objects.create(
        organisation=organisation,
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label[:255],
        before=before,
        after=after,
        note=note,
        made_by=made_by,
    )


def _tags(record_):
    return {
        "area": record_.ai_area,
        "category": record_.ai_category,
        "subcategory": record_.ai_subcategory,
        "sentiment": record_.sentiment,
    }


def correct_classification(organisation, record_, fields, *, made_by, note=""):
    """Apply a person's tags to one interaction and log the correction.

    `fields` may carry any of area/category/subcategory/sentiment, as stored
    values or human labels — the same tolerance the classifier's own parser
    has, so "Bug Report" and "bug_report" both work. A subcategory must sit
    under the chosen category; that is the taxonomy's rule, checked by the
    taxonomy. Stamps `classification_corrected_at`, so a reclassify pass
    leaves the row alone.
    """
    before = _tags(record_)
    after = dict(before)
    lookups = {
        "area": taxonomy.AIArea,
        "category": taxonomy.AICategory,
        "subcategory": taxonomy.AISubcategory,
        "sentiment": taxonomy.Sentiment,
    }
    for key, choices in lookups.items():
        if key not in fields:
            continue
        raw = fields[key]
        if raw in ("", None):
            after[key] = "" if key != "sentiment" else taxonomy.Sentiment.NEUTRAL
            continue
        value = classification._lookup(choices, raw)
        if value is None:
            raise InvalidCorrection(f"{raw!r} is not a {key} the taxonomy has.")
        after[key] = value
    if after["category"] and after["subcategory"]:
        # Refused, not repaired. The classifier's parser repairs a category to
        # match the model's subcategory because a model's slip is cheap to
        # fix; a person picking an inconsistent pair should be told, not
        # quietly overruled on half of what they chose.
        allowed = taxonomy.SUBCATEGORIES_BY_CATEGORY.get(after["category"], ())
        if after["subcategory"] not in allowed:
            raise InvalidCorrection(
                f"{after['subcategory']!r} is not a subcategory of {after['category']!r}."
            )
    if after == before:
        return record_, None

    record_.ai_area = after["area"]
    record_.ai_category = after["category"]
    record_.ai_subcategory = after["subcategory"]
    record_.sentiment = after["sentiment"]
    now = timezone.now()
    record_.ai_classified_at = record_.ai_classified_at or now
    record_.classification_corrected_at = now
    record_.save(
        update_fields=[
            "ai_area",
            "ai_category",
            "ai_subcategory",
            "sentiment",
            "ai_classified_at",
            "classification_corrected_at",
        ]
    )
    label = getattr(record_, "title", None) or getattr(record_, "subject", "") or str(record_.pk)
    entry = record(
        organisation,
        Feedback.Kind.CLASSIFICATION,
        subject_type=record_._meta.model_name,
        subject_id=record_.pk,
        subject_label=label,
        before=before,
        after=after,
        note=note,
        made_by=made_by,
    )
    return record_, entry


def record_proposal_decision(proposal, decision, note, made_by):
    return record(
        proposal.organisation,
        Feedback.Kind.PROPOSAL,
        subject_type="proposal",
        subject_id=proposal.pk,
        subject_label=proposal.title,
        before={"kind": proposal.kind, "action": proposal.action, "rationale": proposal.rationale},
        after={"decision": decision},
        note=note,
        made_by=made_by,
    )


def record_health_override(customer, before_score, after_override, made_by):
    return record(
        customer.organisation,
        Feedback.Kind.HEALTH_OVERRIDE,
        subject_type="customer",
        subject_id=customer.pk,
        subject_label=customer.name,
        before={"health_score": None if before_score is None else str(before_score)},
        after={"health_score_override": None if after_override is None else str(after_override)},
        made_by=made_by,
    )
