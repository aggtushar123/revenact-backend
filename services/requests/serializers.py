from rest_framework import serializers

from services.accounts.models import User

from .models import FeatureRequest, RequestEvidence


class OwnerField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return User.objects.filter(organisation=self.context["request"].user.organisation)

    def to_representation(self, value):
        user = value if isinstance(value, User) else User.objects.get(pk=value.pk)
        return {"id": user.id, "name": user.name}


class FeatureRequestWriteSerializer(serializers.ModelSerializer):
    owner = OwnerField(allow_null=True, required=False)

    class Meta:
        model = FeatureRequest
        fields = ["title", "summary", "status", "owner"]


def request_row(feature, summary: dict) -> dict:
    return {
        "id": feature.id,
        "title": feature.title,
        "summary": feature.summary,
        "status": feature.status,
        "owner": {"id": feature.owner.id, "name": feature.owner.name} if feature.owner else None,
        "arr": summary["arr"],
        "companies": summary["companies"],
        "interactions": summary["interactions"],
        "last_90_days": summary["last_90_days"],
        "previous_90_days": summary["previous_90_days"],
        "created_at": feature.created_at,
        "updated_at": feature.updated_at,
    }


def evidence_row(row: RequestEvidence) -> dict:
    company = row.company
    is_account = row.account_id is not None
    return {
        "id": row.id,
        "kind": row.kind,
        "record_id": row.record_id,
        "snippet": row.snippet,
        "occurred_at": row.occurred_at,
        "company": {
            "type": "account" if is_account else "customer",
            "id": company.id,
            "name": company.name,
        },
    }
