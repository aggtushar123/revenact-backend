from django.urls import path

from . import views

urlpatterns = [
    path("", views.AnomalyListView.as_view(), name="anomaly-list"),
    path("detect/", views.DetectView.as_view(), name="anomaly-detect"),
    path("<int:pk>/", views.AnomalyDetailView.as_view(), name="anomaly-detail"),
]
