from django.urls import path

from . import views

urlpatterns = [
    path(
        "customers/<int:pk>/contributions/",
        views.CustomerContributionListCreateView.as_view(),
        name="customer-contributions",
    ),
    path(
        "customers/<int:pk>/responsible/",
        views.CustomerResponsibleView.as_view(),
        name="customer-responsible",
    ),
    path(
        "contributions/<int:pk>/",
        views.ContributionDetailView.as_view(),
        name="contribution-detail",
    ),
    path(
        "customers/<int:pk>/questions/",
        views.CustomerQuestionListCreateView.as_view(),
        name="customer-questions",
    ),
    path("questions/", views.QuestionListView.as_view(), name="question-list"),
    path("questions/<int:pk>/answer/", views.QuestionAnswerView.as_view(), name="question-answer"),
]
