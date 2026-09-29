from django.urls import path

from . import views

urlpatterns = [
    path(
        "portfolio/export.csv",
        views.AccountPortfolioExportView.as_view(),
        name="accounts-portfolio-export",
    ),
    path("portfolio/", views.AccountPortfolioView.as_view(), name="accounts-portfolio"),
]
