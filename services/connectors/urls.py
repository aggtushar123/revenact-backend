from django.urls import path

from . import views

urlpatterns = [
    path("", views.ConnectorListCreateView.as_view(), name="connector-list-create"),
    path(
        "oauth/<str:provider_key>/callback/",
        views.ConnectorOAuthCallbackView.as_view(),
        name="connector-oauth-callback",
    ),
    path("<int:pk>/", views.ConnectorDetailView.as_view(), name="connector-detail"),
    path("<int:pk>/connect/", views.ConnectorConnectView.as_view(), name="connector-connect"),
    path(
        "<int:pk>/credentials/",
        views.ConnectorCredentialsView.as_view(),
        name="connector-credentials",
    ),
    path("<int:pk>/sync/", views.ConnectorSyncView.as_view(), name="connector-sync"),
    path("<int:pk>/inbound/", views.ConnectorInboundView.as_view(), name="connector-inbound"),
]
