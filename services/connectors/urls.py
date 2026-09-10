from django.urls import path

from . import views

urlpatterns = [
    path("", views.ConnectorListCreateView.as_view(), name="connector-list-create"),
    path("<int:pk>/", views.ConnectorDetailView.as_view(), name="connector-detail"),
]
