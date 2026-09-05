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
]
