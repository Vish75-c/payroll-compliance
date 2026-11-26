"""Record who performed each payroll run action."""
from __future__ import annotations

from .models import PayrollRun, RunEvent


def actor_name(user) -> str:
    if user is None:
        return "system"
    full = user.get_full_name() if hasattr(user, "get_full_name") else ""
    return f"{full} ({user.username})" if full else user.username


def record(run: PayrollRun, user, action: str, message: str, **detail) -> RunEvent:
    return RunEvent.objects.create(run=run, actor=actor_name(user), action=action, message=message, detail=detail)
