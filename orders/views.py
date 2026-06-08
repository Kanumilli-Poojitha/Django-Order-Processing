"""
Minimal views for the orders application.

health_check is used by the Docker Compose healthcheck directive:

    test: ["CMD-SHELL", "curl -f http://localhost:8000/health/ || exit 1"]

Returning HTTP 200 with a JSON body causes ``curl -f`` to exit with
code 0 (success), which Docker Compose interprets as a healthy container.
"""
from django.http import JsonResponse


def health_check(request):
    """Liveness probe endpoint consumed by the Docker healthcheck."""
    return JsonResponse({"status": "ok"})