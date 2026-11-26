from __future__ import annotations

from payroll_engine.money import to_str
from payroll_engine.periods import Period

from payroll.shared.present import _dt, _user

from . import dues
from .models import StatutoryDue


def due(x: StatutoryDue, today=None):
    return {
        "id": x.pk, "run_id": x.run_id, "period": x.period, "period_label": Period.parse(x.period).label, "kind": x.kind,
        "title": dues.title(x), "jurisdiction": x.jurisdiction, "employee_amount": to_str(x.employee_amount),
        "employer_amount": to_str(x.employer_amount), "total": to_str(x.total), "people": x.people,
        "due_date": x.due_date.isoformat(), "paid_on": x.paid_on and x.paid_on.isoformat(), "reference": x.reference,
        "paid_by": _user(x.paid_by), "paid_at": _dt(x.paid_at), "note": x.note, "status": dues.status_of(x, today),
    }
