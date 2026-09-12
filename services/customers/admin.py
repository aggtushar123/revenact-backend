from django.contrib import admin

from .models import (
    Account,
    Activity,
    CalendarEvent,
    Call,
    Canvas,
    Contact,
    Customer,
    Email,
    Headline,
    HealthSnapshot,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
    Ticket,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organisation",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
        "total_contract_value",
    ]
    list_filter = ["organisation", "lifecycle_stage", "ai_pulse_value"]
    search_fields = ["name", "domain"]
    readonly_fields = ["created_at", "updated_at", "created_by", "modified_by"]


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "customers_list",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
        "arr",
    ]
    list_filter = ["customers__organisation", "lifecycle_stage", "ai_pulse_value"]
    search_fields = ["name", "domain", "customers__name"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Customers")
    def customers_list(self, obj):
        # `customers` is a many-to-many now (see the Account model's own
        # docstring) — list_display can't render an M2M field directly.
        return ", ".join(obj.customers.values_list("name", flat=True))


@admin.register(HealthSnapshot)
class HealthSnapshotAdmin(admin.ModelAdmin):
    list_display = ["parent", "captured_on", "health_score", "csm_pulse_score", "ai_pulse_value"]
    list_filter = ["captured_on"]
    search_fields = ["customer__name", "account__name"]
    readonly_fields = ["created_at"]
    date_hierarchy = "captured_on"

    @admin.display(description="Customer / Account")
    def parent(self, obj):
        # Exactly one of the two is set — see the model's own CheckConstraint.
        return obj.customer or obj.account


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["type", "customer", "account", "occurred_at", "links", "watchers"]
    list_filter = ["type"]
    search_fields = ["customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Email)
class EmailAdmin(admin.ModelAdmin):
    list_display = ["subject", "sender_name", "recipient_name", "customer", "account", "sent_at"]
    # The AI taxonomy is filterable here because admin is where a CSM
    # corrects one: classify_interactions writes a best guess, and a wrong
    # area on a handful of emails is worth finding by filtering to it.
    list_filter = ["is_starred", "sentiment", "ai_area", "ai_category"]
    search_fields = ["subject", "sender_name", "recipient_name", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "assignee_name",
        "customer",
        "account",
        "due_date",
        "priority",
        "status",
    ]
    list_filter = ["priority", "status"]
    search_fields = ["title", "assignee_name", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["title", "author_name", "customer", "account", "logged_at", "links"]
    search_fields = ["title", "author_name", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "ticket_number",
        "title",
        "assignee_name",
        "customer",
        "account",
        "status",
        "priority",
        "opened_at",
    ]
    list_filter = ["status", "priority", "sentiment", "ai_area", "ai_category"]
    search_fields = ["ticket_number", "title", "assignee_name", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "host_name",
        "customer",
        "account",
        "occurred_at",
        "duration_minutes",
        "sentiment",
    ]
    list_filter = ["sentiment", "ai_area", "ai_category", "connector"]
    search_fields = ["title", "host_name", "summary", "customer__name", "account__name"]
    readonly_fields = ["created_at", "ai_classified_at"]


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "type",
        "customer",
        "account",
        "event_date",
        "start_time",
        "end_time",
        "attendee_count",
    ]
    list_filter = ["type"]
    search_fields = ["title", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "role",
        "customer",
        "account",
        "status",
        "sentiment",
        "last_contacted_at",
    ]
    list_filter = ["role", "status", "sentiment"]
    search_fields = ["name", "email", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ["title", "stage", "priority", "mrr", "customer", "account"]
    list_filter = ["stage", "priority"]
    search_fields = ["title", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ["title", "stage", "priority", "mrr", "customer", "account"]
    list_filter = ["stage", "priority"]
    search_fields = ["title", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ["survey_type", "status", "score", "sent_at", "customer", "account"]
    list_filter = ["survey_type", "status"]
    search_fields = ["customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Canvas)
class CanvasAdmin(admin.ModelAdmin):
    list_display = ["name", "customer", "account", "updated_at"]
    search_fields = ["name", "customer__name", "account__name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Headline)
class HeadlineAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "status", "customer", "account", "period_end", "generated_at"]
    list_filter = ["kind", "status"]
    search_fields = ["title", "content", "customer__name", "account__name"]
    readonly_fields = ["created_at"]
