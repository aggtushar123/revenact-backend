from django.urls import path

from . import views

urlpatterns = [
    path("", views.McpView.as_view(), name="mcp"),
    path("tokens/", views.McpTokenView.as_view(), name="mcp-tokens"),
    path("tokens/<int:pk>/", views.McpTokenDetailView.as_view(), name="mcp-token-detail"),
]
