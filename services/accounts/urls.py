from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password-reset/", views.ForgotPasswordView.as_view(), name="password-reset"),
    path(
        "password-reset/confirm/", views.ResetPasswordView.as_view(), name="password-reset-confirm"
    ),
    path("me/", views.MeView.as_view(), name="me"),
    path("organisation/", views.OrganisationSettingsView.as_view(), name="organisation-settings"),
    path("me/change-password/", views.ChangePasswordView.as_view(), name="change-password"),
    path("members/", views.MembersListView.as_view(), name="members-list"),
    path("capabilities/", views.CapabilityListView.as_view(), name="capability-list"),
    path("roles/", views.RoleListCreateView.as_view(), name="role-list-create"),
    path("roles/<int:pk>/", views.RoleDetailView.as_view(), name="role-detail"),
    path("users/", views.OrgUserListCreateView.as_view(), name="org-user-list-create"),
    path("users/<int:pk>/", views.OrgUserDetailView.as_view(), name="org-user-detail"),
]
