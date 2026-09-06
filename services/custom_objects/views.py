from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from services.accounts.permissions import IsOrgAdmin

from .models import CustomFieldDefinition, CustomObjectDefinition, CustomObjectRecord
from .serializers import (
    CustomFieldDefinitionSerializer,
    CustomObjectDefinitionSerializer,
    CustomObjectRecordSerializer,
)


class CustomObjectDefinitionListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/custom-objects/definitions/ — every custom
    object type the caller's own organisation has defined (GET, any
    authenticated org member — everyone needs to know what object tabs
    exist to use them, same "read is open, write is admin-gated" shape
    as OrganisationSettingsView), or defines a new one (POST, admin-only
    — same `IsOrgAdmin` gate as member management, method-gated here
    since GET stays open to everyone)."""

    serializer_class = CustomObjectDefinitionSerializer
    pagination_class = None  # a real org's own custom objects — small, unpaginated.

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrgAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return CustomObjectDefinition.objects.filter(
            organisation=self.request.user.organisation
        ).prefetch_related("fields")


class CustomObjectDefinitionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/custom-objects/definitions/<id>/ — same
    read-open/write-admin-only split as the list view above. Deleting a
    definition cascades to its own fields and every record ever created
    against it (see the model's own on_delete=CASCADE) — same
    "deleting the parent really does delete its children" behaviour as
    every other cascading FK in this codebase."""

    serializer_class = CustomObjectDefinitionSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsOrgAdmin()]

    def get_queryset(self):
        return CustomObjectDefinition.objects.filter(organisation=self.request.user.organisation)


class CustomFieldDefinitionListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/custom-objects/definitions/<definition_id>/fields/
    — admin-only POST (adds one field to that object type); GET is open
    to any org member for parity with the definition endpoints, though
    the frontend normally reads fields nested on the definition itself
    rather than calling this separately.

    404s (not 403) for a definition_id outside the caller's own
    organisation, same "check the parent once via get_object_or_404"
    convention as AccountListCreateView's own."""

    serializer_class = CustomFieldDefinitionSerializer
    pagination_class = None  # one object's own fields — always a handful.

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrgAdmin()]
        return [IsAuthenticated()]

    def _object_definition(self):
        return get_object_or_404(
            CustomObjectDefinition,
            pk=self.kwargs["definition_id"],
            organisation=self.request.user.organisation,
        )

    def get_queryset(self):
        return self._object_definition().fields.all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["object_definition"] = self._object_definition()
        return context


class CustomFieldDefinitionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE
    /api/v1/custom-objects/definitions/<definition_id>/fields/<pk>/ —
    admin-only, same reasoning as the field list view's own POST."""

    serializer_class = CustomFieldDefinitionSerializer
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get_queryset(self):
        return CustomFieldDefinition.objects.filter(
            object_definition_id=self.kwargs["definition_id"],
            object_definition__organisation=self.request.user.organisation,
        )


class CustomObjectRecordListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/custom-objects/records/?definition=<id>[&customer=<id>|&account=<id>]
    — `?definition=` is always required; adding `&customer=<id>` or
    `&account=<id>` scopes the GET to one specific parent's own records
    (what CustomObjectsTab.tsx calls, on a Customer/Account's own
    page) — omitting both instead lists every record of that object
    type across the caller's whole organisation, any parent (what the
    org-wide per-object page, pages/customObjects/
    CustomObjectRecordsPage.tsx, calls from the sidebar's own real
    "Custom Objects" section). POST always requires a real parent
    (exactly one of `customer_id`/`account_id` in the body — see
    CustomObjectRecordSerializer's own validation); there's no
    "create without a parent" concept.

    Paginated only for the org-wide case (a Customer/Account's own
    handful of records stays unpaginated, same reasoning as the
    per-object-definition views above) — see `pagination_class`.

    Not admin-gated — adding/viewing a custom object *record* is like
    adding a Task or a Note, open to any org member (see this app's own
    plan/docstring on why only *defining* object/field types is
    admin-only)."""

    serializer_class = CustomObjectRecordSerializer
    permission_classes = [IsAuthenticated]

    @property
    def pagination_class(self):
        has_parent = self.request.query_params.get("customer") or self.request.query_params.get(
            "account"
        )
        # PageNumberPagination alone (no page_size override) picks up the
        # real global REST_FRAMEWORK["PAGE_SIZE"] — same default every
        # other paginated list in this codebase already uses.
        return None if has_parent else PageNumberPagination

    def get_queryset(self):
        definition_id = self.request.query_params.get("definition")
        customer_id = self.request.query_params.get("customer")
        account_id = self.request.query_params.get("account")
        if not definition_id:
            raise ValidationError("?definition=<id> is required.")

        queryset = CustomObjectRecord.objects.filter(
            object_definition_id=definition_id,
            object_definition__organisation=self.request.user.organisation,
        )
        if customer_id:
            queryset = queryset.filter(
                customer_id=customer_id, customer__organisation=self.request.user.organisation
            )
        elif account_id:
            queryset = queryset.filter(
                account_id=account_id,
                account__customers__organisation=self.request.user.organisation,
            )
        else:
            org = self.request.user.organisation
            queryset = queryset.filter(
                Q(customer__organisation=org) | Q(account__customers__organisation=org)
            ).distinct()
        return queryset


class CustomObjectRecordDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/custom-objects/records/<id>/ — scoped to
    the caller's own organisation via either parent, same "match on
    customer OR account, whichever is set" shape the model itself
    uses."""

    serializer_class = CustomObjectRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        org = self.request.user.organisation
        return CustomObjectRecord.objects.filter(
            Q(customer__organisation=org) | Q(account__customers__organisation=org)
        ).distinct()
