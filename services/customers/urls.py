from django.urls import path

from . import views

urlpatterns = [
    path("", views.CustomerListCreateView.as_view(), name="customer-list-create"),
    path("stats/", views.CustomerStatsView.as_view(), name="customer-stats"),
    path(
        "<int:customer_id>/accounts/",
        views.AccountListCreateView.as_view(),
        name="customer-accounts",
    ),
    path(
        "<int:customer_id>/accounts/<int:pk>/",
        views.AccountDetailView.as_view(),
        name="account-detail",
    ),
    path(
        "<int:customer_id>/activities/",
        views.CustomerActivityListView.as_view(),
        name="customer-activities",
    ),
    path(
        "<int:customer_id>/accounts/<int:account_id>/activities/",
        views.AccountActivityListView.as_view(),
        name="account-activities",
    ),
    path(
        "<int:customer_id>/emails/",
        views.CustomerEmailListView.as_view(),
        name="customer-emails",
    ),
    path(
        "<int:customer_id>/accounts/<int:account_id>/emails/",
        views.AccountEmailListView.as_view(),
        name="account-emails",
    ),
    path(
        "<int:customer_id>/tasks/",
        views.CustomerTaskListView.as_view(),
        name="customer-tasks",
    ),
    path(
        "<int:customer_id>/accounts/<int:account_id>/tasks/",
        views.AccountTaskListView.as_view(),
        name="account-tasks",
    ),
    path("<int:pk>/", views.CustomerDetailView.as_view(), name="customer-detail"),
]
