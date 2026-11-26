from __future__ import annotations

from payroll.shared.present import _dt, _user

from ..ai import client as ai_client


def company(c, detail=False):
    out = {"id": c.pk, "code": c.code, "name": c.name, "state": c.state, "city": c.city, "ai_mode": c.ai_mode,
           "pan": c.pan, "tan": c.tan, "pf_establishment_code": c.pf_establishment_code, "esi_employer_code": c.esi_employer_code,
           "pay_day": c.pay_day}
    if detail:
        out.update({"ai_mode_label": c.get_ai_mode_display(), "ai_mode_changed_by": _user(c.ai_mode_changed_by),
                    "ai_mode_changed_at": _dt(c.ai_mode_changed_at), "ai": ai_client.describe()})
    return out
