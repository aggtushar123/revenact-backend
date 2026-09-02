from django.urls import path

from . import views

urlpatterns = [
    path("", views.CustomerListCreateView.as_view(), name="customer-list-create"),
    path("stats/", views.CustomerStatsView.as_view(), name="customer-stats"),
    path("<int:customer_id>/accounts/", views.AccountListView.as_view(), name="customer-accounts"),
    path("<int:pk>/", views.CustomerDetailView.as_view(), name="customer-detail"),
]
