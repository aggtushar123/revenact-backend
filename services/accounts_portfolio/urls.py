from django.urls import path

from . import views

urlpatterns = [
    path("portfolio/", views.AccountPortfolioView.as_view(), name="accounts-portfolio"),
]
