from django.contrib import admin

from .models import (
    Account,
    Activity,
    CalendarEvent,
    Contact,
    Customer,
    Email,
    Note,
    Opportunity,
    Risk,
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
    list_filter = ["organisation", "lifecycle_stage", "ai_pulse_score"]
    search_fields = ["name", "domain"]
    readonly_fields = ["created_at", "updated_at", "created_by", "modified_by"]


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "customer",
        "health_score",
        "lifecycle_stage",
        "owner",
        "renewal_date",
        "arr",
    ]
    list_filter = ["customer__organisation", "lifecycle_stage", "ai_pulse_score"]
    search_fields = ["name", "domain", "customer__name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["type", "customer", "account", "occurred_at", "links", "watchers"]
    list_filter = ["type"]
    search_fields = ["customer__name", "account__name"]
    readonly_fields = ["created_at"]


@admin.register(Email)
class EmailAdmin(admin.ModelAdmin):
    list_display = ["subject", "sender_name", "recipient_name", "customer", "account", "sent_at"]
    list_filter = ["is_starred"]
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
    list_filter = ["status", "priority"]
    search_fields = ["ticket_number", "title", "assignee_name", "customer__name", "account__name"]
    readonly_fields = ["created_at"]


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
