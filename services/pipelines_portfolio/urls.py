"""The Pipelines book's routes, one set per kind. The kind comes from the
route, never from the client, so a view can index `KINDS` directly."""

from django.urls import path

from . import views
from .kinds import KINDS

urlpatterns = [
    path(f"{key}/", views.PipelineView.as_view(), {"kind_key": key}, name=f"pipelines-{key}")
    for key in KINDS
]
