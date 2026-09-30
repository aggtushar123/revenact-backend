"""The Pipelines book's routes, one set per kind. The kind comes from the
route, never from the client, so a view can index `KINDS` directly."""

from django.urls import path

from . import views
from .kinds import KINDS

urlpatterns = [
    route
    for key in KINDS
    for route in (
        path(
            f"{key}/export.csv",
            views.PipelineExportView.as_view(),
            {"kind_key": key},
            name=f"pipelines-{key}-export",
        ),
        path(f"{key}/", views.PipelineView.as_view(), {"kind_key": key}, name=f"pipelines-{key}"),
    )
]
