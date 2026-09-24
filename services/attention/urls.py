from django.urls import path

from . import views

urlpatterns = [
    path("attention/", views.AttentionListView.as_view(), name="attention-list"),
    path("attention/snooze/", views.AttentionSnoozeView.as_view(), name="attention-snooze"),
    # Keys contain `:` (e.g. "renewal:42"), hence `path` rather than `str`. Its
    # own view class, DELETE-only — see AttentionSnoozeDetailView's docstring.
    path(
        "attention/snooze/<path:key>/",
        views.AttentionSnoozeDetailView.as_view(),
        name="attention-unsnooze",
    ),
]
