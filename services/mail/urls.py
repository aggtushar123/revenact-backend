from django.urls import path

from . import views

urlpatterns = [
    path("mail/connection/", views.MyMailboxView.as_view(), name="mailbox"),
    path("mail/connect/<str:provider_key>/", views.ConnectView.as_view(), name="mailbox-connect"),
    path(
        "mail/oauth/<str:provider_key>/callback/",
        views.OAuthCallbackView.as_view(),
        name="mailbox-oauth-callback",
    ),
    path("mail/sync/", views.SyncNowView.as_view(), name="mailbox-sync"),
    path("mail/messages/", views.MailMessageListView.as_view(), name="mail-messages"),
    path("mail/messages/summary/", views.MailSummaryView.as_view(), name="mail-summary"),
    path("mail/messages/<int:pk>/", views.MailMessageDetailView.as_view(), name="mail-message"),
    path("mail/messages/<int:pk>/reply/", views.MailReplyView.as_view(), name="mail-reply"),
    path(
        "customers/<int:customer_id>/emails/send/",
        views.CustomerComposeView.as_view(),
        name="customer-email-send",
    ),
    path(
        "customers/<int:customer_id>/accounts/<int:account_id>/emails/send/",
        views.AccountComposeView.as_view(),
        name="account-email-send",
    ),
]
