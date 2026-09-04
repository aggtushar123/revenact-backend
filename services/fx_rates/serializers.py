from rest_framework import serializers

from .models import FxRate


class FxRateSerializer(serializers.ModelSerializer):
    """`organisation` is set from the request in the view (see
    FxRateListCreateView.perform_create), never client-settable — same
    pattern as WebhookSubscriptionSerializer's own `organisation`."""

    currency_display = serializers.CharField(source="get_currency_display", read_only=True)

    class Meta:
        model = FxRate
        fields = ["id", "currency", "currency_display", "rate_to_org_currency", "updated_at"]
        read_only_fields = ["id", "updated_at"]

    def validate_currency(self, value):
        # A rate for the org's own base currency is meaningless (it's
        # implicitly 1, and storing it invites it to drift out of sync
        # with the real base currency the moment that changes).
        request = self.context["request"]
        if value == request.user.organisation.currency:
            raise serializers.ValidationError(
                "That's already this organisation's own currency — no rate needed."
            )
        # DRF can't auto-generate a UniqueTogetherValidator for
        # (organisation, currency) since `organisation` isn't a
        # serializer field at all (it's set server-side in the view) —
        # check it by hand so a duplicate is a clean 400, not a raw
        # IntegrityError. Only on create: self.instance is set on
        # update, where `currency` is normally left unchanged anyway.
        if (
            self.instance is None
            and FxRate.objects.filter(
                organisation=request.user.organisation, currency=value
            ).exists()
        ):
            raise serializers.ValidationError(
                "A rate for this currency already exists — edit it instead of adding another."
            )
        return value
