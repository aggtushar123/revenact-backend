from django.urls import path

from . import views

urlpatterns = [
    path("", views.ScenarioListCreateView.as_view(), name="scenario-list-create"),
    path("<int:pk>/", views.ScenarioDetailView.as_view(), name="scenario-detail"),
    path("<int:pk>/run/", views.ScenarioRunView.as_view(), name="scenario-run"),
    path("<int:pk>/runs/", views.ScenarioRunListView.as_view(), name="scenario-run-list"),
]
