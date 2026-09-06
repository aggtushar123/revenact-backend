from django.urls import path

from . import views

urlpatterns = [
    path(
        "definitions/",
        views.CustomObjectDefinitionListCreateView.as_view(),
        name="custom-object-definition-list-create",
    ),
    path(
        "definitions/<int:pk>/",
        views.CustomObjectDefinitionDetailView.as_view(),
        name="custom-object-definition-detail",
    ),
    path(
        "definitions/<int:definition_id>/fields/",
        views.CustomFieldDefinitionListCreateView.as_view(),
        name="custom-field-definition-list-create",
    ),
    path(
        "definitions/<int:definition_id>/fields/<int:pk>/",
        views.CustomFieldDefinitionDetailView.as_view(),
        name="custom-field-definition-detail",
    ),
    path(
        "records/",
        views.CustomObjectRecordListCreateView.as_view(),
        name="custom-object-record-list-create",
    ),
    path(
        "records/<int:pk>/",
        views.CustomObjectRecordDetailView.as_view(),
        name="custom-object-record-detail",
    ),
]
