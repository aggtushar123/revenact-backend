"""Mounted at /api/v1/auth/oauth/ beside the password endpoints, so both
sign-in methods live under one prefix."""

from django.urls import path

from . import views

urlpatterns = [
    path("providers/", views.ProviderListView.as_view(), name="oauth-providers"),
    path("exchange/", views.ExchangeView.as_view(), name="oauth-exchange"),
    path("workspace/", views.WorkspaceCreateView.as_view(), name="oauth-workspace-create"),
    path(
        "workspace/preview/", views.WorkspacePreviewView.as_view(), name="oauth-workspace-preview"
    ),
    path("<str:provider_key>/start/", views.StartView.as_view(), name="oauth-start"),
    path("<str:provider_key>/callback/", views.CallbackView.as_view(), name="oauth-callback"),
]
