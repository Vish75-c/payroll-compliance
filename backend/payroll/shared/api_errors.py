"""Turn business-rule errors into clear 400/403 answers for the screens."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler

from .permissions import RuleViolation


def exception_handler(exc, context):
    if isinstance(exc, RuleViolation):
        code = status.HTTP_403_FORBIDDEN if exc.code in ("forbidden", "four_eyes") else status.HTTP_400_BAD_REQUEST
        if exc.code == "workflow_unavailable":
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Response({"detail": exc.message, "code": exc.code}, status=code)
    return drf_handler(exc, context)
