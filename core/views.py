from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Liveness check. No auth required — used for local dev sanity checks
    and can double as an uptime probe once deployed."""
    return Response(
        {
            "status": "ok",
            "service": "revenact-backend",
            "time": timezone.now().isoformat(),
        }
    )
