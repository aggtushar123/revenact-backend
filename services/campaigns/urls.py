from django.urls import path

from . import views

urlpatterns = [
    path("", views.CampaignListCreateView.as_view(), name="campaign-list-create"),
    path("<int:pk>/", views.CampaignDetailView.as_view(), name="campaign-detail"),
    path("<int:pk>/send/", views.CampaignSendView.as_view(), name="campaign-send"),
]
