from django.urls import path

from . import views

urlpatterns = [
    path(
        "portfolio/export.csv",
        views.PortfolioExportView.as_view(),
        name="organizations-portfolio-export",
    ),
    path("portfolio/", views.PortfolioView.as_view(), name="organizations-portfolio"),
]
