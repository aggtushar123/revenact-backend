"""Mounted at /api/v1/billing/."""

from django.urls import path

from . import views

urlpatterns = [
    path("account/", views.AccountView.as_view(), name="billing-account"),
    path("ledger/", views.LedgerView.as_view(), name="billing-ledger"),
    path("plans/", views.PlanListView.as_view(), name="billing-plans"),
]
