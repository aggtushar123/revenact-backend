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
    path("<int:pk>/", views.CustomerDetailView.as_view(), name="customer-detail"),
]
