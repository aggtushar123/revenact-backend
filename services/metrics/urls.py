from django.urls import path

from . import views

urlpatterns = [
    path("", views.MetricListView.as_view(), name="metric-list"),
    path("<str:key>/history/", views.MetricHistoryView.as_view(), name="metric-history"),
]
