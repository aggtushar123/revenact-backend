from django.urls import path

from . import views

urlpatterns = [
    path("conversations/", views.ConversationListView.as_view(), name="copilot-conversation-list"),
    path(
        "conversations/<int:pk>/",
        views.ConversationDetailView.as_view(),
        name="copilot-conversation-detail",
    ),
    path("messages/", views.SendMessageView.as_view(), name="copilot-send-message"),
    path("usage/", views.ModelUsageView.as_view(), name="copilot-model-usage"),
    path("usage/budgets/", views.ModelBudgetView.as_view(), name="copilot-model-budget"),
    path("skills/", views.SkillsView.as_view(), name="copilot-skills"),
    path(
        "conversations/<int:pk>/session/",
        views.SessionView.as_view(),
        name="copilot-session",
    ),
    path(
        "conversations/<int:pk>/session/invite/",
        views.SessionInviteCreateView.as_view(),
        name="copilot-session-invite",
    ),
    path(
        "conversations/<int:pk>/session/handoff/",
        views.SessionHandoffView.as_view(),
        name="copilot-session-handoff",
    ),
    path(
        "conversations/<int:pk>/session/decisions/",
        views.SessionDecisionsView.as_view(),
        name="copilot-session-decisions",
    ),
    path(
        "conversations/<int:pk>/session/close/",
        views.SessionCloseView.as_view(),
        name="copilot-session-close",
    ),
    path("sessions/invites/", views.MyInvitesView.as_view(), name="copilot-my-invites"),
    path(
        "sessions/invites/<int:pk>/respond/",
        views.RespondToInviteView.as_view(),
        name="copilot-invite-respond",
    ),
]
