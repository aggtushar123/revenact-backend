"""Mounted at /api/v1/platform/. Staff only; see views.py."""

from django.urls import path

from . import views

urlpatterns = [
    path("overview/", views.OverviewView.as_view(), name="platform-overview"),
    path("organisations/", views.OrganisationListView.as_view(), name="platform-organisations"),
    path(
        "organisations/<int:pk>/",
        views.OrganisationDetailView.as_view(),
        name="platform-organisation",
    ),
    path(
        "organisations/<int:pk>/status/",
        views.OrganisationStatusView.as_view(),
        name="platform-organisation-status",
    ),
    path(
        "organisations/<int:pk>/owner/",
        views.OrganisationOwnerView.as_view(),
        name="platform-organisation-owner",
    ),
    path(
        "organisations/<int:pk>/billing/",
        views.OrganisationBillingView.as_view(),
        name="platform-organisation-billing",
    ),
    path(
        "organisations/<int:pk>/billing/<str:action>/",
        views.OrganisationBillingActionView.as_view(),
        name="platform-organisation-billing-action",
    ),
    path("staff/", views.StaffListView.as_view(), name="platform-staff"),
]
