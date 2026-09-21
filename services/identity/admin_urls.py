"""Mounted at /api/v1/identity/. Everything here is scoped to the caller's own
tenant, derived from their membership: there is no organisation id in any path,
so there is none to tamper with."""

from django.urls import path

from . import admin_views

urlpatterns = [
    path("domains/", admin_views.DomainListCreateView.as_view(), name="identity-domains"),
    path(
        "domains/<int:pk>/verify/",
        admin_views.DomainVerifyView.as_view(),
        name="identity-domain-verify",
    ),
    path(
        "access-requests/",
        admin_views.AccessRequestListView.as_view(),
        name="identity-access-requests",
    ),
    path(
        "access-requests/<int:pk>/<str:decision>/",
        admin_views.AccessRequestDecisionView.as_view(),
        name="identity-access-request-decision",
    ),
    path(
        "invitations/",
        admin_views.InvitationListCreateView.as_view(),
        name="identity-invitations",
    ),
    path(
        "invitations/<int:pk>/cancel/",
        admin_views.InvitationCancelView.as_view(),
        name="identity-invitation-cancel",
    ),
]
