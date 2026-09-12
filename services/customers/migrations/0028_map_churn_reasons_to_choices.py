"""Map the free-text churn reasons onto the closed list that replaces them.

Runs **before** the column shrinks to 32 characters (0029), because a reason
somebody typed can be 255 characters long and the point of this migration is
that none of it is thrown away.

The mapping is deliberately frozen here rather than imported from the app: it
describes the state of one book on one day, and a keyword list that quietly
changed underneath a historical migration would make the same database
migrate two different ways. `classify` is importable so it can be tested.

Anything the keywords cannot place becomes `other` **and the original wording
is prepended to `churn_comment`**, which is where the nuance lives now. A
reason nobody can classify is still the only record of why a customer left,
and this migration is the last moment it exists.
"""

from django.db import migrations

#: Checked in order — the first keyword found in the reason wins, so the more
#: specific phrases come before the words that appear inside them.
KEYWORDS = [
    ("out of business", "shut_down"),
    ("shut down", "shut_down"),
    ("shutdown", "shut_down"),
    ("bankrupt", "shut_down"),
    ("ceased", "shut_down"),
    ("acquir", "acquired"),
    ("merge", "acquired"),
    ("consolidat", "consolidation"),
    ("champion", "champion_left"),
    ("sponsor", "champion_left"),
    ("budget", "budget"),
    ("funding", "budget"),
    ("cost cut", "budget"),
    ("too expensive", "price"),
    ("price", "price"),
    ("pricing", "price"),
    ("cost", "price"),
    ("competitor", "competitor"),
    ("switched to", "competitor"),
    ("rival", "competitor"),
    ("feature", "product_gap"),
    ("missing", "product_gap"),
    ("capabilit", "product_gap"),
    ("functionalit", "product_gap"),
    ("roadmap", "product_gap"),
    ("adopt", "adoption"),
    ("onboard", "adoption"),
    ("engagement", "adoption"),
    ("never used", "adoption"),
    ("usage", "adoption"),
    ("support", "support"),
    ("service quality", "support"),
    ("outage", "support"),
]


def classify(raw):
    """The stored value for one free-text reason.

    Returns `("", False)` for a blank reason — nobody recorded one, which is
    not the same as "Other" and must not become it. The second item says
    whether the original wording still needs keeping.
    """
    text = (raw or "").strip()
    if not text:
        return "", False

    lowered = text.casefold()
    for keyword, value in KEYWORDS:
        if keyword in lowered:
            return value, False
    return "other", True


def forwards(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")

    for customer in Customer.objects.exclude(churn_reason="").iterator():
        value, keep_wording = classify(customer.churn_reason)
        if value == customer.churn_reason:
            continue

        if keep_wording:
            note = f'Reason as originally typed: "{customer.churn_reason.strip()}"'
            customer.churn_comment = (
                f"{note}\n\n{customer.churn_comment}" if customer.churn_comment else note
            )
        customer.churn_reason = value
        customer.save(update_fields=["churn_reason", "churn_comment"])


def backwards(apps, schema_editor):
    """Put the labels back, so the column holds something a human typed.

    Not a true reverse — the exact wording is gone unless it was kept in
    `churn_comment` — and it says so rather than pretending.
    """
    Customer = apps.get_model("customers", "Customer")
    labels = {
        "price": "Price",
        "budget": "Budget cut",
        "product_gap": "Missing capability",
        "adoption": "Never adopted",
        "competitor": "Switched to a competitor",
        "champion_left": "Champion left",
        "acquired": "Acquired or merged",
        "shut_down": "Went out of business",
        "consolidation": "Vendor consolidation",
        "support": "Service or support",
        "other": "Other",
    }
    for customer in Customer.objects.exclude(churn_reason="").iterator():
        label = labels.get(customer.churn_reason)
        if label:
            customer.churn_reason = label
            customer.save(update_fields=["churn_reason"])


class Migration(migrations.Migration):
    dependencies = [("customers", "0027_email_ai_area_email_ai_category_and_more")]

    operations = [migrations.RunPython(forwards, backwards)]
