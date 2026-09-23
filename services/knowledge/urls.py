from django.urls import path

from . import views

urlpatterns = [
    path(
        "customers/<int:pk>/contributions/",
        views.CustomerContributionListCreateView.as_view(),
        name="customer-contributions",
    ),
    path(
        "customers/<int:pk>/brief/",
        views.AccountBriefView.as_view(),
        name="customer-brief",
    ),
    path("knowledge/gaps/", views.KnowledgeGapListView.as_view(), name="knowledge-gaps"),
    path(
        "knowledge/gaps/<int:pk>/answer/",
        views.KnowledgeGapAnswerView.as_view(),
        name="knowledge-gap-answer",
    ),
    path(
        "knowledge/gaps/<int:pk>/dismiss/",
        views.KnowledgeGapDismissView.as_view(),
        name="knowledge-gap-dismiss",
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
    path("knowledge/activity/", views.KnowledgeActivityView.as_view(), name="knowledge-activity"),
    path("questions/<int:pk>/answer/", views.QuestionAnswerView.as_view(), name="question-answer"),
]
