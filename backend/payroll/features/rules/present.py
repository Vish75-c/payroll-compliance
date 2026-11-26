from __future__ import annotations

from payroll.shared.present import _dt, _user


def policy(v, used: set[str]):
    return {
        "id": v.pk, "scope": v.scope, "key": v.key, "jurisdiction": v.jurisdiction, "effective_from": v.effective_from.isoformat(),
        "payload": v.payload, "title": v.title, "note": v.note, "created_by": _user(v.created_by), "created_at": _dt(v.created_at),
        "company_id": v.company_id, "used_by_final_month": str(v.pk) in used,
    }
