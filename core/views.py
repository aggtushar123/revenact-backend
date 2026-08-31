from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
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
