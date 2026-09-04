from django.urls import path

from . import views

urlpatterns = [
    path("", views.FxRateListCreateView.as_view(), name="fx-rate-list-create"),
    path("<int:pk>/", views.FxRateDetailView.as_view(), name="fx-rate-detail"),
]
