from django.urls import path

from . import views

urlpatterns = [
    path("attention/", views.AttentionListView.as_view(), name="attention-list"),
    # Keys contain `:` (e.g. "renewal:42"), hence `path` rather than `str`.
    path(
        "attention/snooze/<path:key>/",
        views.AttentionSnoozeView.as_view(),
        name="attention-unsnooze",
    ),
    path("attention/snooze/", views.AttentionSnoozeView.as_view(), name="attention-snooze"),
]
