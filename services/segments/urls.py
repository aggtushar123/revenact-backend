from django.urls import path

from . import views

urlpatterns = [
    path("", views.SegmentListCreateView.as_view(), name="segment-list"),
    path("preview/", views.SegmentPreviewView.as_view(), name="segment-preview"),
    path("<int:pk>/", views.SegmentDetailView.as_view(), name="segment-detail"),
    path("<int:pk>/duplicate/", views.SegmentDuplicateView.as_view(), name="segment-duplicate"),
    path("<int:pk>/members/", views.SegmentMembersView.as_view(), name="segment-members"),
    path(
        "<int:pk>/members/export.csv",
        views.SegmentMembersExportView.as_view(),
        name="segment-members-export",
    ),
    path(
        "<int:pk>/members/<int:record_id>/",
        views.SegmentMemberView.as_view(),
        name="segment-member",
    ),
]
