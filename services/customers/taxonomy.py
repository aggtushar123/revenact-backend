"""The AI taxonomy — what the AI Trending Topics dashboard groups by.

Three independent dimensions, all of them about one *interaction* (an email, a
call, or a ticket):

    area         which side of the business owns it — 3 values
    category     what kind of thing it is            — 10 values
    subcategory  the specific flavour of that kind   — belongs to one category

Area is deliberately **not** derived from category. The frontend mock this
replaces showed "Bug Report" under both Product & Growth and Customer Success,
and "Onboarding" under both Support & Operations and Customer Success — which is
right: who owns a conversation and what the conversation is about are two
different questions. A one-to-many hierarchy between them would force the
dashboard's Area donut and its Category bar to be the same chart twice.

Subcategory *is* inside category, and that one is enforced (see
`validate_classification`, called from every classified model's `clean()`). "API
Issue" under "Onboarding" isn't a judgement call, it's a contradiction — and the
dashboard's two bar charts are read together, so a subcategory that doesn't roll
up into the category beside it makes both of them lie.

The vocabulary itself comes from the mock (see the frontend's
ActivitiesByAICategoryBar / ActivitiesByAISubCategoryBar / ActivityDetailedTable)
rather than being invented here, so the dashboard keeps showing the buckets it
was designed around. It is closed on purpose: a free-text label would let the
model invent a new bucket on every call and the Category bar would grow a long
tail of synonyms — "Bug", "Bug Report", "bug_report" — that nobody can chart.
Adding a value is a migration, which is the right amount of friction for a
dimension the whole dashboard is keyed on.
"""

from django.db import models


class Sentiment(models.TextChoices):
    """How a conversation sounds. The same three values as Contact.sentiment —
    deliberately the identical vocabulary rather than a second one for the same
    idea, since "how does this feel" means the same thing about a support
    ticket, a call and a person.

    Lives here rather than on the model that first had it (Ticket) now that all
    three interaction types carry it and one dashboard charts them together."""

    POSITIVE = "positive", "Positive"
    NEUTRAL = "neutral", "Neutral"
    NEGATIVE = "negative", "Negative"


class AIArea(models.TextChoices):
    """Which side of the business a conversation belongs to."""

    PRODUCT_GROWTH = "product_growth", "Product & Growth"
    SUPPORT_OPERATIONS = "support_operations", "Support & Operations"
    CUSTOMER_SUCCESS = "customer_success", "Customer Success"


class AICategory(models.TextChoices):
    """What kind of conversation it is."""

    ONBOARDING = "onboarding", "Onboarding"
    BUG_REPORT = "bug_report", "Bug Report"
    WORKFLOW_AUTOMATION = "workflow_automation", "Workflow Automation"
    INTEGRATION_SUPPORT = "integration_support", "Integration Support"
    SYSTEM_NOTIFICATION = "system_notification", "System Notification"
    ACCOUNT_MANAGEMENT = "account_management", "Account Management"
    FEATURE_REQUEST = "feature_request", "Feature Request"
    CUSTOMER_FEEDBACK = "customer_feedback", "Customer Feedback"
    REPORTING_ANALYTICS = "reporting_analytics", "Reporting & Analytics"
    SECURITY_COMPLIANCE = "security_compliance", "Security & Compliance"


class AISubcategory(models.TextChoices):
    """The specific flavour. Each value belongs to exactly one category —
    see SUBCATEGORIES_BY_CATEGORY below, which is the authority on which."""

    # Onboarding
    SETUP_ASSISTANCE = "setup_assistance", "Setup Assistance"
    TRAINING_REQUEST = "training_request", "Training Request"
    DATA_IMPORT = "data_import", "Data Import"
    # Bug Report
    PERFORMANCE_ISSUE = "performance_issue", "Performance Issue"
    BACKEND_FAILURE = "backend_failure", "Backend Failure"
    UI_BUG = "ui_bug", "UI Bug"
    # Workflow Automation
    RULE_MISFIRE = "rule_misfire", "Rule Misfire"
    TRIGGER_SETUP = "trigger_setup", "Trigger Setup"
    # Integration Support
    API_ISSUE = "api_issue", "API Issue"
    WEBHOOK_FAILURE = "webhook_failure", "Webhook Failure"
    THIRD_PARTY_CONNECTOR = "third_party_connector", "Third-party Connector"
    # System Notification
    ALERT_FATIGUE = "alert_fatigue", "Alert Fatigue"
    NOTIFICATION_DELIVERY = "notification_delivery", "Notification Delivery"
    EMAIL_DELIVERY = "email_delivery", "Email Delivery"
    # Account Management
    PLAN_UPGRADE = "plan_upgrade", "Plan Upgrade"
    USER_ACCESS = "user_access", "User Access"
    BILLING_QUESTION = "billing_question", "Billing Question"
    # Feature Request
    UI_ENHANCEMENT = "ui_enhancement", "UI Enhancement"
    NEW_INTEGRATION = "new_integration", "New Integration"
    # Customer Feedback
    PRODUCT_PRAISE = "product_praise", "Product Praise"
    USABILITY_FEEDBACK = "usability_feedback", "Usability Feedback"
    # Reporting & Analytics
    EXPORT_PROBLEM = "export_problem", "Export Problem"
    DASHBOARD_REQUEST = "dashboard_request", "Dashboard Request"
    # Security & Compliance
    DATA_PRIVACY = "data_privacy", "Data Privacy"
    ACCESS_REVIEW = "access_review", "Access Review"


#: Which subcategories each category admits. The single source of truth for the
#: hierarchy — `validate_classification` reads it, the classifier prompt is
#: built from it, and the dashboard's filter options are derived from it, so the
#: three can't drift apart.
SUBCATEGORIES_BY_CATEGORY = {
    AICategory.ONBOARDING: (
        AISubcategory.SETUP_ASSISTANCE,
        AISubcategory.TRAINING_REQUEST,
        AISubcategory.DATA_IMPORT,
    ),
    AICategory.BUG_REPORT: (
        AISubcategory.PERFORMANCE_ISSUE,
        AISubcategory.BACKEND_FAILURE,
        AISubcategory.UI_BUG,
    ),
    AICategory.WORKFLOW_AUTOMATION: (
        AISubcategory.RULE_MISFIRE,
        AISubcategory.TRIGGER_SETUP,
    ),
    AICategory.INTEGRATION_SUPPORT: (
        AISubcategory.API_ISSUE,
        AISubcategory.WEBHOOK_FAILURE,
        AISubcategory.THIRD_PARTY_CONNECTOR,
    ),
    AICategory.SYSTEM_NOTIFICATION: (
        AISubcategory.ALERT_FATIGUE,
        AISubcategory.NOTIFICATION_DELIVERY,
        AISubcategory.EMAIL_DELIVERY,
    ),
    AICategory.ACCOUNT_MANAGEMENT: (
        AISubcategory.PLAN_UPGRADE,
        AISubcategory.USER_ACCESS,
        AISubcategory.BILLING_QUESTION,
    ),
    AICategory.FEATURE_REQUEST: (
        AISubcategory.UI_ENHANCEMENT,
        AISubcategory.NEW_INTEGRATION,
    ),
    AICategory.CUSTOMER_FEEDBACK: (
        AISubcategory.PRODUCT_PRAISE,
        AISubcategory.USABILITY_FEEDBACK,
    ),
    AICategory.REPORTING_ANALYTICS: (
        AISubcategory.EXPORT_PROBLEM,
        AISubcategory.DASHBOARD_REQUEST,
    ),
    AICategory.SECURITY_COMPLIANCE: (
        AISubcategory.DATA_PRIVACY,
        AISubcategory.ACCESS_REVIEW,
    ),
}

#: The reverse lookup, built once. Used by the classifier to repair a
#: model answer that named a subcategory and the wrong parent category.
CATEGORY_BY_SUBCATEGORY = {
    sub: category for category, subs in SUBCATEGORIES_BY_CATEGORY.items() for sub in subs
}

assert set(CATEGORY_BY_SUBCATEGORY) == set(AISubcategory), (
    "every subcategory must belong to exactly one category"
)


def validate_classification(*, ai_category, ai_subcategory):
    """Returns an error message, or None when the pair is consistent.

    Returns rather than raises so the caller decides what a bad pair means:
    `clean()` turns it into a ValidationError on the right field, while the
    classifier treats it as an answer to repair rather than a failure.

    A subcategory with no category is the one asymmetric case — it is rejected,
    because a subcategory that doesn't roll up anywhere can't be charted under
    the category bar beside it. The reverse (a category with no subcategory) is
    fine: "this is a bug report and we don't know more than that" is a real
    state, and the Subcategory chart simply doesn't count it.
    """

    if not ai_subcategory:
        return None
    if not ai_category:
        return "A subcategory needs its category set too."
    allowed = SUBCATEGORIES_BY_CATEGORY.get(AICategory(ai_category), ())
    if AISubcategory(ai_subcategory) not in allowed:
        return (
            f"{AISubcategory(ai_subcategory).label} isn't a subcategory of "
            f"{AICategory(ai_category).label}."
        )
    return None
