from __future__ import annotations

from payroll.shared.present import _dt, _user

from .client import RULE_BASED, fallback_notice


def _once_each(checks: list[dict]) -> list[dict]:
    """The same check stored several times (older entries got one per confirmation) is shown once, with a count."""
    seen: dict[tuple, dict] = {}
    for check in checks or []:
        key = (check.get("level"), check.get("message"))
        if key in seen:
            seen[key]["repeated"] = seen[key].get("repeated", 1) + 1
        else:
            seen[key] = dict(check)
    out = []
    for check in seen.values():
        if check.get("repeated"):
            check["message"] = f"{check['message']} ({check['repeated']} times)"
        out.append(check)
    return out


def ai(i, full=False):
    if i is None:
        return None
    checks = i.validation
    if i.error and i.provider == RULE_BASED:
        # Older entries include provider diagnostics in their fallback warning.
        checks = [
            {**check, "message": fallback_notice(i.error)}
            if check.get("level") == "warning" and check.get("message", "").endswith(f": {i.error}")
            else check
            for check in checks
        ]
    checks = _once_each(checks)
    out = {
        "id": i.pk, "purpose": i.purpose, "purpose_label": i.get_purpose_display(), "provider": i.provider, "model": i.model,
        "status": i.status, "status_label": i.get_status_display(), "response": i.response, "validation": checks,
        "created_at": _dt(i.created_at), "reviewed_by": _user(i.reviewed_by), "reviewed_at": _dt(i.reviewed_at),
        "reused_saved_answer": getattr(i, "reused", False), "reuse_count": i.reuse_count, "latency_ms": i.latency_ms,
    }
    if full:
        out["request"] = i.request
        out["usage"] = i.usage
        out["input_hash"] = i.input_hash
    return out
