from django.urls import path

from . import views

urlpatterns = [
    path("", views.FeatureRequestListView.as_view(), name="feature-request-list"),
    path("gather/", views.GatherView.as_view(), name="feature-request-gather"),
    path("<int:pk>/", views.FeatureRequestDetailView.as_view(), name="feature-request-detail"),
    path("<int:pk>/merge/", views.MergeView.as_view(), name="feature-request-merge"),
    path("evidence/<int:pk>/move/", views.EvidenceMoveView.as_view(), name="request-evidence-move"),
    path(
        "evidence/<int:pk>/dismiss/",
        views.EvidenceDismissView.as_view(),
        name="request-evidence-dismiss",
    ),
]
