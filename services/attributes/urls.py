from django.urls import path

from . import views

urlpatterns = [
    path(
        "definitions/", views.AIAttributeListCreateView.as_view(), name="ai-attribute-list-create"
    ),
    path(
        "definitions/<int:pk>/", views.AIAttributeDetailView.as_view(), name="ai-attribute-detail"
    ),
    path("definitions/<int:pk>/fill/", views.FillView.as_view(), name="ai-attribute-fill"),
    path("values/", views.ValueListView.as_view(), name="ai-attribute-values"),
    path("values/history/", views.ValueHistoryView.as_view(), name="ai-attribute-value-history"),
]
