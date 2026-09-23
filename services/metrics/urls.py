from django.urls import path

from . import views

urlpatterns = [
    path("", views.MetricListView.as_view(), name="metric-list"),
    # Before the keyed routes: "signals" is not a metric key.
    path("signals/", views.MetricSignalsView.as_view(), name="metric-signals"),
    path("graph/", views.GraphView.as_view(), name="metric-graph"),
    path("brief/", views.BriefView.as_view(), name="metric-brief"),
    path("brief/generate/", views.BriefGenerateView.as_view(), name="metric-brief-generate"),
    path("brief/schedule/", views.BriefScheduleView.as_view(), name="metric-brief-schedule"),
    path(
        "brief/schedule/send/",
        views.BriefSendNowView.as_view(),
        name="metric-brief-schedule-send",
    ),
    path("feedback/", views.FeedbackListView.as_view(), name="feedback-list"),
    path("proposals/", views.ProposalListView.as_view(), name="proposal-list"),
    path("proposals/generate/", views.ProposalGenerateView.as_view(), name="proposal-generate"),
    path(
        "proposals/<int:pk>/<str:decision>/",
        views.ProposalDecisionView.as_view(),
        name="proposal-decision",
    ),
    path("initiatives/", views.InitiativeListCreateView.as_view(), name="initiative-list"),
    path("initiatives/<int:pk>/", views.InitiativeDetailView.as_view(), name="initiative-detail"),
    path("<str:key>/history/", views.MetricHistoryView.as_view(), name="metric-history"),
    path(
        "<str:key>/explanation/", views.MetricExplanationView.as_view(), name="metric-explanation"
    ),
    path("<str:key>/explain/", views.MetricExplainView.as_view(), name="metric-explain"),
    path("<str:key>/by/<str:dimension>/", views.MetricSliceView.as_view(), name="metric-slice"),
]
