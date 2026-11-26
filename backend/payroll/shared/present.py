from __future__ import annotations

from django.utils import timezone


def _user(u):
    if not u:
        return None
    return {"id": u.pk, "username": u.username, "name": (u.get_full_name() or u.username)}


def _dt(value):
    return timezone.localtime(value).isoformat() if value else None
