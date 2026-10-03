from django.urls import path

from . import views

urlpatterns = [
    path("", views.SegmentListCreateView.as_view(), name="segment-list"),
    path("<int:pk>/", views.SegmentDetailView.as_view(), name="segment-detail"),
    path("<int:pk>/duplicate/", views.SegmentDuplicateView.as_view(), name="segment-duplicate"),
]
