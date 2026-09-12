"""Dev/demo convenience — not part of the product. Fills in the AI taxonomy on
seeded emails, calls and tickets **without calling a model**, so the AI Trending
Topics dashboard is full on a fresh demo database with no API key configured.

This is not the real classifier. `manage.py classify_interactions` is — it sends
each record to Claude and costs money per batch. This one matches keywords in a
record's own text against a small table, which is enough to make the demo's
charts coherent (a ticket about "Export to CSV not including all columns" lands
in Reporting & Analytics → Export Problem, where a reader would put it) and is
free, instant and identical on every run.

Anything the keyword table doesn't recognise gets a deterministic bucket derived
from the record's own id, so the long tail still populates the charts instead of
leaving them lopsided toward whatever happens to be matchable.

It writes `ai_classified_at` like the real classifier does, which means
`classify_interactions` will then skip these rows — that's the point: a demo
database shouldn't produce a four-figure model bill the first time someone runs
the real command. Pass `--reclassify` to that command to have it redo them
anyway.

Idempotent, and **only touches unclassified rows** unless `--force` is given, so
a record corrected by hand in admin stays corrected.

Usage:
    python manage.py seed_demo_classifications --org-email alice@acme.io
    python manage.py seed_demo_classifications --org-email alice@acme.io --force
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from services.accounts.models import User
from services.customers import taxonomy
from services.customers.models import Call, Email, Ticket

A = taxonomy.AIArea
C = taxonomy.AICategory
S = taxonomy.AISubcategory

#: `(keywords, category, subcategory, area)`, first match wins.
#:
#: Ordered symptom-before-subject, which is the ordering that matters: "Dashboard
#: loading slow" is a performance bug, not a dashboard request, so the "slow" rule
#: has to be reached before the "dashboard" one. Likewise the narrow systems come
#: before the broad ones — "webhook" before "integration", "export" before
#: "report" — or the specific rules below them are unreachable.
KEYWORD_RULES = (
    # Named systems and symptoms first.
    (("webhook",), C.INTEGRATION_SUPPORT, S.WEBHOOK_FAILURE, A.SUPPORT_OPERATIONS),
    (("api", "rate limit"), C.INTEGRATION_SUPPORT, S.API_ISSUE, A.SUPPORT_OPERATIONS),
    (
        ("slow", "timeout", "timing out", "performance", "latency"),
        C.BUG_REPORT,
        S.PERFORMANCE_ISSUE,
        A.PRODUCT_GROWTH,
    ),
    (
        ("500", "crash", "outage", "broken", "down"),
        C.BUG_REPORT,
        S.BACKEND_FAILURE,
        A.PRODUCT_GROWTH,
    ),
    (("export", "csv", "download"), C.REPORTING_ANALYTICS, S.EXPORT_PROBLEM, A.PRODUCT_GROWTH),
    (
        ("dashboard", "report", "analytics"),
        C.REPORTING_ANALYTICS,
        S.DASHBOARD_REQUEST,
        A.PRODUCT_GROWTH,
    ),
    (
        ("salesforce", "hubspot", "integration", "connector", "sync"),
        C.INTEGRATION_SUPPORT,
        S.THIRD_PARTY_CONNECTOR,
        A.SUPPORT_OPERATIONS,
    ),
    (
        ("sso", "login", "permission", "access", "seat"),
        C.ACCOUNT_MANAGEMENT,
        S.USER_ACCESS,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("invoice", "billing", "payment"),
        C.ACCOUNT_MANAGEMENT,
        S.BILLING_QUESTION,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("security", "soc 2", "gdpr", "privacy", "residency"),
        C.SECURITY_COMPLIANCE,
        S.DATA_PRIVACY,
        A.SUPPORT_OPERATIONS,
    ),
    (("audit", "review access"), C.SECURITY_COMPLIANCE, S.ACCESS_REVIEW, A.SUPPORT_OPERATIONS),
    (("ui", "display", "rendering", "layout"), C.BUG_REPORT, S.UI_BUG, A.PRODUCT_GROWTH),
    (
        ("automation", "workflow", "rule", "trigger"),
        C.WORKFLOW_AUTOMATION,
        S.RULE_MISFIRE,
        A.PRODUCT_GROWTH,
    ),
    (
        ("alert", "notification", "digest"),
        C.SYSTEM_NOTIFICATION,
        S.ALERT_FATIGUE,
        A.SUPPORT_OPERATIONS,
    ),
    (
        ("email delivery", "bounce", "spam"),
        C.SYSTEM_NOTIFICATION,
        S.EMAIL_DELIVERY,
        A.SUPPORT_OPERATIONS,
    ),
    # Then the lifecycle conversations, which are about the account rather than
    # about a thing that went wrong.
    (
        ("onboarding", "kickoff", "implementation", "import"),
        C.ONBOARDING,
        S.DATA_IMPORT,
        A.SUPPORT_OPERATIONS,
    ),
    (
        ("training", "enablement", "walkthrough", "admin team"),
        C.ONBOARDING,
        S.TRAINING_REQUEST,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("setup", "configure", "getting started"),
        C.ONBOARDING,
        S.SETUP_ASSISTANCE,
        A.SUPPORT_OPERATIONS,
    ),
    (
        ("renewal", "upgrade", "expansion", "plan", "contract"),
        C.ACCOUNT_MANAGEMENT,
        S.PLAN_UPGRADE,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("qbr", "business review", "check-in", "alignment"),
        C.ACCOUNT_MANAGEMENT,
        S.PLAN_UPGRADE,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("feature request", "roadmap", "would like", "wish"),
        C.FEATURE_REQUEST,
        S.UI_ENHANCEMENT,
        A.PRODUCT_GROWTH,
    ),
    (
        ("feedback", "survey", "nps", "thank"),
        C.CUSTOMER_FEEDBACK,
        S.PRODUCT_PRAISE,
        A.CUSTOMER_SUCCESS,
    ),
    (
        ("escalation", "complaint", "frustrat", "unacceptable"),
        C.CUSTOMER_FEEDBACK,
        S.USABILITY_FEEDBACK,
        A.CUSTOMER_SUCCESS,
    ),
)

#: The fallback rotation for text no rule matches, spread across categories so
#: the unmatched tail doesn't all pile into one bar.
FALLBACK = (
    (C.ACCOUNT_MANAGEMENT, S.PLAN_UPGRADE, A.CUSTOMER_SUCCESS),
    (C.ONBOARDING, S.SETUP_ASSISTANCE, A.SUPPORT_OPERATIONS),
    (C.BUG_REPORT, S.UI_BUG, A.PRODUCT_GROWTH),
    (C.INTEGRATION_SUPPORT, S.API_ISSUE, A.SUPPORT_OPERATIONS),
    (C.SYSTEM_NOTIFICATION, S.NOTIFICATION_DELIVERY, A.SUPPORT_OPERATIONS),
    (C.WORKFLOW_AUTOMATION, S.TRIGGER_SETUP, A.PRODUCT_GROWTH),
    (C.FEATURE_REQUEST, S.NEW_INTEGRATION, A.PRODUCT_GROWTH),
    (C.CUSTOMER_FEEDBACK, S.USABILITY_FEEDBACK, A.CUSTOMER_SUCCESS),
    (C.REPORTING_ANALYTICS, S.DASHBOARD_REQUEST, A.PRODUCT_GROWTH),
    (C.SECURITY_COMPLIANCE, S.ACCESS_REVIEW, A.SUPPORT_OPERATIONS),
)

#: Words that colour a record's sentiment when it has no real one. Deliberately
#: small: the demo wants a believable mix, not a sentiment engine.
NEGATIVE_WORDS = ("escalat", "outage", "frustrat", "unacceptable", "broken", "complaint", "churn")
POSITIVE_WORDS = ("thank", "great", "happy", "praise", "excited", "renewed", "love")


def _text_of(record):
    name = record._meta.model_name
    if name == "email":
        return f"{record.subject} {record.body}"
    if name == "call":
        return f"{record.title} {record.summary}"
    return record.title


def classify_text(text, *, fallback_key):
    """`(category, subcategory, area)` for one record's text.

    Exposed (and tested) rather than private because it is the whole behaviour
    of this command: the keyword table's job is to be predictable, and a test
    that asserts "export to CSV" lands in Export Problem is what keeps it that
    way while rules get added above it."""

    haystack = text.casefold()
    for keywords, category, subcategory, area in KEYWORD_RULES:
        if any(word in haystack for word in keywords):
            return category, subcategory, area
    return FALLBACK[fallback_key % len(FALLBACK)]


def sentiment_for(text, *, current, fallback_key):
    """Leaves a deliberate non-neutral sentiment alone, and colours the rest.

    `sentiment` defaults to "neutral", so a neutral value can't be told apart
    from one nobody set — which is exactly why this only overwrites neutral.
    A ticket the ticket seeder marked negative keeps that, and the Ticket
    Overview dashboard's own numbers don't move underneath it."""

    if current and current != taxonomy.Sentiment.NEUTRAL:
        return current
    haystack = text.casefold()
    if any(word in haystack for word in NEGATIVE_WORDS):
        return taxonomy.Sentiment.NEGATIVE
    if any(word in haystack for word in POSITIVE_WORDS):
        return taxonomy.Sentiment.POSITIVE
    # A 2-in-5 positive / 3-in-5 neutral split over the unmatched tail, by id,
    # so the sentiment donut isn't one solid neutral ring.
    return taxonomy.Sentiment.POSITIVE if fallback_key % 5 in (0, 1) else taxonomy.Sentiment.NEUTRAL


class Command(BaseCommand):
    help = "Fills in the AI taxonomy on demo interactions, with keywords rather than a model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Also redo records that already carry a classification.",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc

        org = caller.organisation
        now = timezone.now()
        totals = {}

        for model in (Email, Call, Ticket):
            queryset = model.objects.filter(
                Q(customer__organisation=org) | Q(account__customers__organisation=org)
            ).distinct()
            if not options["force"]:
                queryset = queryset.filter(ai_classified_at__isnull=True)

            written = 0
            for record in queryset.iterator(chunk_size=500):
                text = _text_of(record)
                category, subcategory, area = classify_text(text, fallback_key=record.pk)
                record.ai_category = category
                record.ai_subcategory = subcategory
                record.ai_area = area
                record.sentiment = sentiment_for(
                    text, current=record.sentiment, fallback_key=record.pk
                )
                record.ai_classified_at = now
                record.save(
                    update_fields=[
                        "ai_category",
                        "ai_subcategory",
                        "ai_area",
                        "sentiment",
                        "ai_classified_at",
                    ]
                )
                written += 1
            totals[model._meta.model_name] = written

        summary = ", ".join(f"{count} {name}(s)" for name, count in totals.items())
        self.stdout.write(self.style.SUCCESS(f"{org.name}: classified {summary}."))
