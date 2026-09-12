from django.urls import path

from . import views

urlpatterns = [
    path("", views.MetricListView.as_view(), name="metric-list"),
    # Before the keyed routes: "signals" is not a metric key.
    path("signals/", views.MetricSignalsView.as_view(), name="metric-signals"),
    path("brief/", views.BriefView.as_view(), name="metric-brief"),
    path("brief/generate/", views.BriefGenerateView.as_view(), name="metric-brief-generate"),
    path("initiatives/", views.InitiativeListCreateView.as_view(), name="initiative-list"),
    path("initiatives/<int:pk>/", views.InitiativeDetailView.as_view(), name="initiative-detail"),
    path("<str:key>/history/", views.MetricHistoryView.as_view(), name="metric-history"),
    path("<str:key>/by/<str:dimension>/", views.MetricSliceView.as_view(), name="metric-slice"),
]
