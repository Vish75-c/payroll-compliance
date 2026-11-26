from __future__ import annotations

from django.conf import settings

from payroll.shared.permissions import is_in_group
from payroll.shared.present import _user

from ..ai import client as ai_client


def me(user):
    return {
        "authenticated": True,
        "user": _user(user),
        "roles": [r for r in ("maker", "checker") if is_in_group(user, r)],
        "ai": ai_client.describe(),
        "workflow_backend": settings.WORKFLOW_BACKEND,
    }
