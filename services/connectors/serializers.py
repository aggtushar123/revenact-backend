from rest_framework import serializers

from services.customers.models import Account, Customer

from .models import Connector


class ConnectorSerializer(serializers.ModelSerializer):
    """See Connector's own docstring.

    `customer_ids`/`account_ids` are write-only id lists, mirroring
    `AccountSerializer.customer_ids` — the read side returns
    `{id, name}` pairs so a picker can render without a second call.
    Both are optional, and omitting them entirely leaves the connector
    organisation-wide, which is the whole point of the scope rule.

    `organisation` is never a serializer field — the view sets it from
    `request.user`, same as every other org-scoped serializer here."""

    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    is_organisation_wide = serializers.BooleanField(read_only=True)
    # What the connector has actually brought in — from the views' own
    # annotations (see `with_ingested`), so a page of connectors costs one
    # query. A connector is not a live sync (see the model), so these are
    # the records already attributed to it, and `last_record_at` is the
    # newest of a ticket's opened date and a call's time.
    ticket_count = serializers.IntegerField(read_only=True, default=0)
    call_count = serializers.IntegerField(read_only=True, default=0)
    last_record_at = serializers.SerializerMethodField()
    customers = serializers.SerializerMethodField()
    accounts = serializers.SerializerMethodField()
    customer_ids = serializers.PrimaryKeyRelatedField(
        source="customers",
        queryset=Customer.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    account_ids = serializers.PrimaryKeyRelatedField(
        source="accounts",
        queryset=Account.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = Connector
        fields = [
            "id",
            "provider",
            "provider_display",
            "name",
            "is_enabled",
            "customers",
            "accounts",
            "customer_ids",
            "account_ids",
            "is_organisation_wide",
            "ticket_count",
            "call_count",
            "last_record_at",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def get_last_record_at(self, obj):
        latest = [
            d
            for d in (getattr(obj, "last_ticket_at", None), getattr(obj, "last_call_at", None))
            if d is not None
        ]
        return max(latest).isoformat() if latest else None

    def get_customers(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.customers.all()]

    def get_accounts(self, obj):
        return [{"id": a.id, "name": a.name} for a in obj.accounts.all()]

    def validate_customer_ids(self, customers):
        organisation = self.context["request"].user.organisation
        outside = [c for c in customers if c.organisation_id != organisation.id]
        if outside:
            raise serializers.ValidationError(
                "Every linked organization must be in your own organisation."
            )
        return customers

    def validate_account_ids(self, accounts):
        organisation = self.context["request"].user.organisation
        outside = [
            a for a in accounts if not a.customers.filter(organisation=organisation).exists()
        ]
        if outside:
            raise serializers.ValidationError(
                "Every linked account must be in your own organisation."
            )
        return accounts

    def validate(self, attrs):
        """Hand-rolled uniqueness on (organisation, provider, name).

        DRF can't auto-generate a UniqueTogetherValidator for it,
        because `organisation` isn't a serializer field at all — same
        situation, and same fix, as FxRateSerializer.validate_currency.
        Without this a duplicate surfaces as a raw IntegrityError 500
        instead of a clean 400."""

        organisation = self.context["request"].user.organisation
        provider = attrs.get("provider", getattr(self.instance, "provider", None))
        name = attrs.get("name", getattr(self.instance, "name", None))

        clash = Connector.objects.filter(organisation=organisation, provider=provider, name=name)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {"name": "You already have a connector with this name for this provider."}
            )
        return attrs
