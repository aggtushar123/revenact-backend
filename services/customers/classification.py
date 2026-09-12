"""Asks Claude where an interaction sits in the taxonomy.

The AI Trending Topics dashboard groups emails, calls and tickets by AI Area /
Category / Subcategory (see taxonomy.py). Those fields start blank; this is what
fills them in, and the only thing in this codebase that does.

Its own module for the same reason headline_generation.py is one: a single home
for "how do we build this prompt and read the answer back", trivially mockable —
every test patches `classify_batch` or `get_completion` rather than making a
real, paid call. It reuses services.copilot's client rather than adding a second
one, so the provider choice, credentials and error taxonomy stay settled in one
place.

**Batched, not one call per record.** A tenant has thousands of emails; one
request per row would be thousands of round trips and thousands of copies of the
same taxonomy in the prompt. Records go up in groups of BATCH_SIZE, each tagged
with a `ref` the answer has to echo back, so one reply classifies many rows and a
reply that drops or reorders items still lands on the right ones.

**A bad answer costs one record, never the batch.** The model returns closed-set
values, and it will occasionally return a label instead of a value, a
subcategory under the wrong parent category, or a bucket that doesn't exist.
Each of those is repaired or dropped per item (see `_coerce`); the rest of the
batch is still written. The alternative — raising on the first odd value — would
mean one unusual email blocking a nightly run for everything behind it.
"""

import json

from django.utils import timezone

from services.copilot.anthropic_client import get_completion

from . import taxonomy

#: How many records go up in one request. Twenty fits comfortably inside one
#: prompt alongside the taxonomy, and keeps a single bad reply cheap to redo.
BATCH_SIZE = 20

#: How much of a record's text the model sees. A long email thread's first few
#: hundred characters carry the subject matter; the rest is quoted history, and
#: sending it would multiply the bill for no extra signal.
MAX_TEXT_CHARS = 600


def _options_block():
    """The taxonomy, rendered for the prompt from the taxonomy module itself.

    Built rather than written out by hand so a value added to taxonomy.py can
    never be a value the model is not told about — the failure that would look
    like "the model refuses to use the new category"."""

    areas = "\n".join(f"- {value}  ({label})" for value, label in taxonomy.AIArea.choices)
    categories = []
    for category, subs in taxonomy.SUBCATEGORIES_BY_CATEGORY.items():
        names = ", ".join(sub.value for sub in subs)
        categories.append(f"- {category.value}  ({category.label}) → subcategory one of: {names}")
    return (
        f"AREA, one of:\n{areas}\n\n"
        f"CATEGORY with its permitted SUBCATEGORY values:\n" + "\n".join(categories)
    )


SYSTEM_PROMPT = """You are a customer-success analyst tagging customer \
interactions in a CS platform so they can be charted.

You will be given a numbered list of interactions — emails, calls and support \
tickets from real customer accounts. For each one, decide:

- "area": which side of the business owns the conversation.
- "category": what kind of conversation it is.
- "subcategory": the specific flavour. It MUST be one of the values listed \
under the category you chose.
- "sentiment": positive, neutral or negative — how the customer sounds, not \
whether the news is good for us. A calm bug report is neutral; an apology for \
a late reply is not negative.

Answer with a JSON array and nothing else. One object per interaction, each \
with keys "ref", "area", "category", "subcategory", "sentiment". Echo "ref" \
back exactly as given.

Use only the values listed below, exactly as spelled. If an interaction is too \
thin to place, omit it from the array rather than guessing — a missing tag is \
recoverable, a wrong one is charted as fact.

{options}"""


def build_prompt(records):
    """`records` is a list of `(ref, kind, text)`. The ref is what ties an
    answer back to a row, so it is the caller's own stable handle (see
    `_ref_for`), never the model's to invent."""

    lines = []
    for ref, kind, text in records:
        lines.append(f"[{ref}] {kind}: {text[:MAX_TEXT_CHARS]}")
    return "Classify these interactions:\n\n" + "\n\n".join(lines)


def _ref_for(record):
    """`email:41` — type and primary key. Unique across the three models, which
    one batch deliberately mixes: a batch is a slice of "everything
    unclassified", and splitting it by model would pay for three prompts where
    one does."""
    return f"{record._meta.model_name}:{record.pk}"


def _text_for(record):
    """What the model reads. Per model, because the interesting text lives in a
    different field on each — and the fields nobody renders (Email.watchers,
    Ticket.links) are no more interesting to a classifier than to a card."""

    name = record._meta.model_name
    if name == "email":
        return f"{record.subject}. {record.body}"
    if name == "call":
        return f"{record.title}. {record.summary}" if record.summary else record.title
    # Ticket: the title is the whole description this model stores — see
    # Ticket's own docstring on the `description` the mock carried and no
    # component ever rendered. Priority and status give the model the
    # severity it can't read from a one-line title.
    return f"{record.title} (priority: {record.priority}, status: {record.status})"


#: What each model is called in the prompt. Plain English rather than the model
#: name: "support ticket" tells the model what it is reading, "ticket" is also a
#: word for a concert pass.
KIND_LABELS = {"email": "Email", "call": "Call", "ticket": "Support ticket"}


def _lookup(choices_class, raw):
    """Accepts the stored value or the human label, case-insensitively, and
    returns None for anything else.

    Labels are accepted because the prompt shows both and a model given
    "product_growth  (Product & Growth)" will sometimes answer with the half
    that reads like English. Rejecting that would throw away a correct answer
    over its formatting."""

    if not isinstance(raw, str):
        return None
    needle = raw.strip().casefold()
    for value, label in choices_class.choices:
        if needle in (value.casefold(), label.casefold()):
            return value
    return None


def _coerce(item):
    """One raw answer object → `{area, category, subcategory, sentiment}`, or
    None when there's nothing usable in it.

    Three repairs, each for a thing models actually do:

    - An unknown area/sentiment becomes blank/neutral rather than sinking the
      item: the other dimensions it got right are still worth charting.
    - A subcategory that doesn't belong to the category it was sent with is
      trusted over that category, because the subcategory is the more specific
      claim and its parent is then unambiguous (CATEGORY_BY_SUBCATEGORY).
    - A subcategory with no recognisable category is kept with its parent
      filled in, for the same reason.

    An item with neither a category nor a subcategory is dropped. Sentiment
    alone is not what this pass is for, and writing `ai_classified_at` on a row
    whose taxonomy is still blank would hide it from the next run."""

    if not isinstance(item, dict):
        return None

    category = _lookup(taxonomy.AICategory, item.get("category"))
    subcategory = _lookup(taxonomy.AISubcategory, item.get("subcategory"))

    if subcategory:
        parent = taxonomy.CATEGORY_BY_SUBCATEGORY[taxonomy.AISubcategory(subcategory)].value
        if category != parent:
            category = parent
    if not category:
        return None

    return {
        "ai_area": _lookup(taxonomy.AIArea, item.get("area")) or "",
        "ai_category": category,
        "ai_subcategory": subcategory or "",
        "sentiment": (
            _lookup(taxonomy.Sentiment, item.get("sentiment")) or taxonomy.Sentiment.NEUTRAL.value
        ),
    }


def _parse(raw):
    """The model's text → a list of objects, forgiving the two wrappers it
    sometimes adds: a ```json fence, or an object with the array inside it."""

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's answer wasn't JSON: {exc}") from exc

    if isinstance(payload, dict):
        for key in ("interactions", "classifications", "results", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
        raise ValueError("The model returned a JSON object with no array in it.")
    if not isinstance(payload, list):
        raise ValueError("The model returned JSON, but not an array.")
    return payload


def classify_batch(records):
    """One model call for up to BATCH_SIZE records.

    Returns `{ref: fields}` for the records the model placed, which may be
    fewer than were sent — the prompt tells it to omit what it can't judge, and
    `_coerce` drops what can't be read. The caller writes only what comes back.

    Lets CopilotNotConfigured/CopilotRequestFailed through untouched, the same
    way generate_headlines does, so the command can report a missing API key as
    a missing API key rather than as zero classifications."""

    prompt_records = [
        (_ref_for(record), KIND_LABELS[record._meta.model_name], _text_for(record))
        for record in records
    ]
    raw = get_completion(
        system=SYSTEM_PROMPT.format(options=_options_block()),
        messages=[{"role": "user", "content": build_prompt(prompt_records)}],
    )

    sent = {ref for ref, _, _ in prompt_records}
    out = {}
    for item in _parse(raw):
        if not isinstance(item, dict):
            continue
        ref = item.get("ref")
        # A ref that wasn't in this batch is discarded rather than looked up:
        # a hallucinated id would otherwise let one batch write a
        # classification onto a record nobody asked about.
        if ref not in sent:
            continue
        fields = _coerce(item)
        if fields:
            out[ref] = fields
    return out


def clear_classification(record):
    """Blanks a record's tags and stamps `ai_classified_at` anyway.

    For a reclassify pass where the model declined to place a record it had
    placed before: "looked at, could not say" is the honest state, and leaving
    the previous answer in place would keep a category nobody stands behind on
    the dashboard. Stamped rather than nulled so a scheduled default pass
    doesn't pay to retry a record whose text hasn't changed."""

    record.ai_area = ""
    record.ai_category = ""
    record.ai_subcategory = ""
    record.ai_classified_at = timezone.now()
    record.save(update_fields=["ai_area", "ai_category", "ai_subcategory", "ai_classified_at"])


def apply_classification(record, fields):
    """Writes one record's tags and stamps `ai_classified_at`.

    Saved field by field rather than with a full `save()` so a concurrent edit
    to the same row's other columns — a ticket being resolved while a nightly
    classification runs — isn't clobbered."""

    for field, value in fields.items():
        setattr(record, field, value)
    record.ai_classified_at = timezone.now()
    record.save(update_fields=[*fields, "ai_classified_at"])
