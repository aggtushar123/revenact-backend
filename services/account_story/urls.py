"""The account page's routes (`/accounts/:id`), mounted at /api/v1/accounts/.

The per-account tabs are the nested /customers/<cid>/accounts/<id>/… view
classes, reached by the account alone (`scoping.get_url_account`): the viewer
may open an account but none of its organisations. None of these routes is
"" or "stats/", so /accounts/ and /accounts/stats/ still resolve."""

from django.urls import path

from services.customers import views as customers

urlpatterns = [
    path("<int:pk>/", customers.AccountDetailView.as_view(), name="account-page-detail"),
    path(
        "<int:account_id>/contacts/",
        customers.AccountContactListView.as_view(),
        name="account-page-contacts",
    ),
    path(
        "<int:account_id>/opportunities/",
        customers.AccountOpportunityListView.as_view(),
        name="account-page-opportunities",
    ),
    path(
        "<int:account_id>/risks/",
        customers.AccountRiskListView.as_view(),
        name="account-page-risks",
    ),
    path(
        "<int:account_id>/files/",
        customers.AccountFileListView.as_view(),
        name="account-page-files",
    ),
    path(
        "<int:account_id>/calls/",
        customers.AccountCallListView.as_view(),
        name="account-page-calls",
    ),
    path(
        "<int:account_id>/surveys/",
        customers.AccountSurveyListView.as_view(),
        name="account-page-surveys",
    ),
    path(
        "<int:account_id>/tasks/",
        customers.AccountTaskListView.as_view(),
        name="account-page-tasks",
    ),
    path(
        "<int:account_id>/notes/",
        customers.AccountNoteListView.as_view(),
        name="account-page-notes",
    ),
]
