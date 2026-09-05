from django.contrib import admin

from .models import (
    Conversation,
    CopilotSession,
    Message,
    SessionEvent,
    SessionInvite,
    SessionParticipant,
)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["role", "content", "created_at"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["title", "organisation", "user", "updated_at"]
    list_filter = ["organisation"]
    search_fields = ["title"]
    inlines = [MessageInline]


class SessionParticipantInline(admin.TabularInline):
    model = SessionParticipant
    extra = 0
    readonly_fields = ["user", "joined_at", "left_at"]


class SessionInviteInline(admin.TabularInline):
    model = SessionInvite
    extra = 0
    readonly_fields = ["invited_user", "invited_by", "status", "created_at", "responded_at"]


class SessionEventInline(admin.TabularInline):
    model = SessionEvent
    extra = 0
    readonly_fields = ["kind", "actor", "message", "payload", "created_at"]


@admin.register(CopilotSession)
class CopilotSessionAdmin(admin.ModelAdmin):
    list_display = ["conversation", "customer", "account", "status", "created_at"]
    list_filter = ["status"]
    inlines = [SessionParticipantInline, SessionInviteInline, SessionEventInline]
