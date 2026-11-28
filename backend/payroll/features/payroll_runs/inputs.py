"""Gather everything one month's calculation needs into a single JSON input.

This is where the database is read. The engine then gets a plain dict and nothing else,
so a stored input can be calculated again at any time and must give the same result.
"""
from __future__ import annotations

import copy
from datetime import date

from payroll_engine import policies as engine_policies
from payroll_engine.money import d, to_str
from payroll_engine.periods import Period, Window, parse_date

from ..hr_data.normalize import unpaid_days_for
from ..onboarding.models import OpeningBalance
from ..rules.policies import versions_for
from .change_kinds import EmployeeMonth, kind_of
from .cycle import month_windows
from .models import ChangeRequest, PayrollRun, RunEmployee


def _in_period(emp: dict, window: Window) -> bool:
    doj = parse_date(emp.get("date_of_joining"))
    doe = parse_date(emp.get("date_of_exit"))
    if doj and doj > window.end:
        return False
    if doe and doe < window.start:
        return False
    return True


def _known_compensation(rows: list[dict], knowledge_date: date) -> list[dict]:
    """Salary records HR had recorded by ``knowledge_date``.

    This is how a late raise turns into arrears: May was paid before the raise was recorded,
    so May's stored result has the old salary, and July (which knows the raise) pays the gap.
    """
    known = [r for r in rows if not r.get("recorded_on") or parse_date(r["recorded_on"]) <= knowledge_date]
    if not known and rows:
        known = [min(rows, key=lambda r: r.get("effective_from") or "")]
    # Two records starting on the same day: the later one recorded is a correction and must win. The engine takes the
    # last record of a start date, so order by start date, then by when it was recorded.
    known = sorted(known, key=lambda r: (r.get("effective_from") or "", r.get("recorded_on") or ""))
    return [{"effective_from": r.get("effective_from"), "components": r["components"]} for r in known]


def override_versions(run: PayrollRun, versions: list[dict]) -> list[dict]:
    """What-if setting values of a draft re-run, as extra versions that beat stored ones for this month only."""
    out = []
    period = run.period_obj
    for key, values in sorted((run.policy_overrides or {}).items()):
        current = engine_policies.resolve_company(versions, key, period)
        payload = copy.deepcopy(current.payload)
        payload.update(values)
        out.append({
            "id": f"what-if:{key}",
            "key": key,
            "scope": engine_policies.OVERRIDE,
            "jurisdiction": "",
            "effective_from": period.start.isoformat(),
            "payload": payload,
            "title": engine_policies.COMPANY_KEYS.get(key, key),
            "recorded_at": "",
        })
    return out


def default_day_count(versions: list[dict], period: Period, cycle_days: int) -> int:
    """What one day's pay is divided by under the company setting, for this month. ``cycle_days``: the days in the
    default pay group's cycle (each group's own cycle is used for its people when the setting is "days in the month")."""
    divisor = engine_policies.resolve_company(versions, "lop", period).get("divisor", "days_in_month")
    return cycle_days if divisor == "days_in_month" else int(d(divisor))


def day_count_versions(run: PayrollRun, versions: list[dict]) -> list[dict]:
    """The day count set when the month was opened, as a version that beats the company setting for this month
    only. It is stored with the month's input, so arrears for this month later use the same day count."""
    if not run.day_count:
        return []
    period = run.period_obj
    payload = copy.deepcopy(engine_policies.resolve_company(versions, "lop", period).payload)
    payload.update({"divisor": str(run.day_count), "source": f"set for {period.label}"})
    return [{
        "id": f"month:{period}:lop",
        "key": "lop",
        "scope": engine_policies.MONTH,
        "jurisdiction": "",
        "effective_from": period.start.isoformat(),
        "payload": payload,
        "title": f"Days for one day's pay, set for {period.label}",
        "recorded_at": "",
    }]


def opening_balances(run: PayrollRun) -> dict[str, OpeningBalance]:
    fy_start = date(run.period_obj.fy_start_year, 4, 1)
    return {b.employee_code: b for b in OpeningBalance.objects.filter(company=run.company, fy_start=fy_start)}


def confirmed_changes(run: PayrollRun) -> list[ChangeRequest]:
    return list(
        ChangeRequest.objects.filter(company=run.company, period=run.period, status=ChangeRequest.CONFIRMED).order_by("id")
    )


def carried_changes(run: PayrollRun, finals: list[PayrollRun]) -> list[ChangeRequest]:
    """Confirmed changes of months that are now final. Their salary changes and their late unpaid days
    keep counting in later months: otherwise a later month would see the earlier one as overpaid (or the
    recovered days as owed) and undo them."""
    periods = [r.period for r in finals]
    return list(ChangeRequest.objects.filter(company=run.company, status=ChangeRequest.CONFIRMED, period__in=periods)
                .order_by("period", "id"))


def _final_runs(run: PayrollRun) -> list[PayrollRun]:
    return list(
        PayrollRun.objects.filter(
            company=run.company, run_type=PayrollRun.TYPE_REGULAR, status=PayrollRun.FINALIZED, period__lt=run.period
        ).order_by("period")
    )


def held_months(run: PayrollRun, finals: list[PayrollRun]) -> dict[str, list[RunEmployee]]:
    """Earlier final months in which a person was on hold (not paid), whether or not that salary is paid since."""
    final_ids = [r.pk for r in finals]
    out: dict[str, list[RunEmployee]] = {}
    for row in (RunEmployee.objects.filter(run_id__in=final_ids, status__in=[RunEmployee.ON_HOLD, RunEmployee.PAID_LATER])
                .select_related("run").order_by("run__period")):
        out.setdefault(row.employee_code, []).append(row)
    return out


def _paid_later_for(code: str, period: str, finals: list[PayrollRun], final_emp: dict) -> dict:
    """What later final months already paid, as arrears, for one person's month."""
    earned: dict = {}
    gross = pf_emp = pf_er = eps_er = d(0)
    for later in finals:
        if later.period <= period:
            continue
        for a in ((final_emp[later.period].get(code) or {}).get("facts", {}).get("arrears_detail", [])):
            if a["period"] != period:
                continue
            for k, v in a["components"].items():
                earned[k] = earned.get(k, d(0)) + d(v)
            gross += d(a["total"])
            pf_emp += d(a["pf_employee"])
            pf_er += d(a["pf_employer"])
            eps_er += d(a.get("eps_employer"))
    return {"earned": {k: to_str(v) for k, v in earned.items()}, "regular_gross": to_str(gross), "pf_employee": to_str(pf_emp),
            "pf_employer": to_str(pf_er), "eps_employer": to_str(eps_er)}


def build_run_input(run: PayrollRun) -> dict:
    company = run.company
    period = run.period_obj
    data = run.snapshot.data
    all_versions = versions_for(company)
    # Only earnings settings are used when projecting future months for TDS.
    versions = []
    for version in all_versions:
        start = parse_date(version["effective_from"])
        projection_setting = version["scope"] == engine_policies.COMPANY and version["key"] in ("lop", "rounding")
        if start <= (period.fy_last.start if projection_setting else period.start):
            versions.append(version)
    versions += day_count_versions(run, versions)
    versions += override_versions(run, versions)
    # Each person's dates: their pay group's cycle, or a transition period when it changed.
    windows = month_windows(company, period, data["employees"])
    arrears_policy = engine_policies.resolve_company(versions, "arrears_policy", period)
    lookback = int(arrears_policy.get("lookback_months", 12))

    changes = confirmed_changes(run)
    finals = _final_runs(run)
    carried = carried_changes(run, finals)
    final_emp = {r.period: {e["code"]: e for e in (r.result or {}).get("employees", [])} for r in finals}
    final_inputs = {r.period: {e["code"]: e for e in (r.input or {}).get("employees", [])} for r in finals}
    final_versions = {r.period: (r.input or {}).get("policy_versions", []) for r in finals}
    openings = opening_balances(run)
    esi_period_start = period.esi_contribution_start
    held = held_months(run, finals)
    past_windows: dict[str, dict] = {}  # the people's dates in an earlier month, for paying a held month
    leave_from = min((r["start_date"] for r in data.get("leave", [])), default=None)

    employees = []
    incomplete = data.get("incomplete") or {}
    for emp in data["employees"]:
        if emp["code"] in incomplete:
            continue  # missing a fact pay depends on (salary, joining date, work state, gender): waits, never paid on a guess
        window = windows[emp["code"]]
        if not _in_period(emp, window.salary):
            continue
        code = emp["code"]
        comp_rows = [r for r in data["compensation"] if r["employee_code"] == code]
        compensation = _known_compensation(comp_rows, run.knowledge_date)
        month = EmployeeMonth(compensation=compensation, unpaid=unpaid_days_for(data, code, period, window.attendance))
        applied = []
        for ch in carried:
            for item in ch.proposal.get("items", []):
                if item.get("employee_code") == code:
                    kind_of(item["type"]).carry(item, month)
        for ch in changes:
            for item in ch.proposal.get("items", []):
                if item.get("employee_code") != code:
                    continue
                kind_of(item["type"]).apply(item, month, ch)
                applied.append(f"change:{ch.pk}")
        compensation, one_time, late_unpaid = month.compensation, month.one_time, month.late_unpaid
        # unpaid absences entered by hand come on top of the HR data
        unpaid = min(month.unpaid + month.extra_unpaid, d(window.salary.days))

        ytd_months = []
        covered_esi_months = []
        arrears_basis = []
        for past in finals:
            past_period = past.period_obj
            paid = final_emp[past.period].get(code)
            if not paid:
                continue
            facts = paid["facts"]
            if past_period.same_fy(period):
                ytd_months.append({"period": past.period, "gross": facts["gross"], "taxable_gross": facts.get("taxable_gross", facts["gross"]),
                                   "tds": facts["tds"]})
            amounts = paid["amounts"]
            if past_period >= esi_period_start and facts.get("esi_covered", d(amounts.get("esi_wage")) > 0):
                covered_esi_months.append(past.period)
            if past_period.months_until(period) > lookback:
                continue
            earned = {k: d(v) for k, v in facts["earned"].items()}
            regular_gross = d(facts.get("regular_gross", amounts["regular_gross"]))
            pf_emp = d(facts["pf_employee_regular"])
            pf_er = d(facts["pf_employer_regular"])
            # Older runs stored regular EPS in amounts and allocated all arrears to EPF.
            eps_er = d(facts.get("eps_employer_regular", amounts["eps_employer"]))
            # Arrears for this month already paid in later months count as paid.
            for later in finals:
                if later.period <= past.period:
                    continue
                later_emp = final_emp[later.period].get(code)
                for a in (later_emp or {}).get("facts", {}).get("arrears_detail", []):
                    if a["period"] != past.period:
                        continue
                    for k, v in a["components"].items():
                        earned[k] = earned.get(k, d(0)) + d(v)
                    regular_gross += d(a["total"])
                    pf_emp += d(a["pf_employee"])
                    pf_er += d(a["pf_employer"])
                    eps_er += d(a.get("eps_employer"))
            past_input = final_inputs[past.period].get(code, {})
            late = late_unpaid.get(past.period, d(0))
            arrears_basis.append({
                "period": past.period,
                "policy_versions": final_versions[past.period],
                "unpaid_days": to_str(d(past_input.get("unpaid_days", "0")) + late),
                **({"late_unpaid_days": to_str(late)} if late else {}),
                "date_of_joining": past_input.get("date_of_joining", emp.get("date_of_joining")),
                "date_of_exit": past_input.get("date_of_exit", emp.get("date_of_exit")),
                "pf_applicable": past_input.get("pf_applicable", emp.get("pf_applicable", True)),
                **({"pay_window": past_input["pay_window"]} if past_input.get("pay_window") else {}),
                "paid": {
                    "earned": {k: to_str(v) for k, v in earned.items()},
                    "regular_gross": to_str(regular_gross),
                    "pf_employee": to_str(pf_emp),
                    "pf_employer": to_str(pf_er),
                    "eps_employer": to_str(eps_er),
                },
            })

        for row in held.get(code, []):
            # A month the person was on hold: not in that month's result, so the loop above skipped it. Paid now as
            # arrears against what was paid for it so far (nothing, until a later month paid it).
            past = row.run.period_obj
            still_unpaid = row.status == RunEmployee.ON_HOLD
            if not still_unpaid and past.months_until(period) > lookback:
                continue
            if row.run.period not in past_windows:
                past_windows[row.run.period] = month_windows(company, past, data["employees"])
            past_window = past_windows[row.run.period][code]
            late = late_unpaid.get(row.run.period, d(0))
            arrears_basis.append({
                "period": row.run.period,
                "policy_versions": (row.run.input or {}).get("policy_versions", []),
                "unpaid_days": to_str(unpaid_days_for(data, code, past, past_window.attendance) + late),
                **({"late_unpaid_days": to_str(late)} if late else {}),
                "date_of_joining": emp.get("date_of_joining"),
                "date_of_exit": emp.get("date_of_exit"),
                "pf_applicable": emp.get("pf_applicable", True),
                "pay_window": past_window.as_input(),
                "paid": _paid_later_for(code, row.run.period, finals, final_emp),
                **({"held": True, "hold_reason": row.hold_reason,
                    "leave_covered": leave_from is None or leave_from <= past.end.isoformat()} if still_unpaid else {}),
            })
        arrears_basis.sort(key=lambda b: b["period"])

        employees.append({
            "code": code,
            "name": emp.get("name", code),
            "work_state": emp.get("work_state", ""),
            "gender": emp.get("gender", ""),
            "date_of_joining": emp.get("date_of_joining"),
            "date_of_exit": emp.get("date_of_exit"),
            "pf_applicable": emp.get("pf_applicable", True),
            "esi_applicable": emp.get("esi_applicable", True),
            "covered_esi_months": covered_esi_months,
            "compensation": sorted(compensation, key=lambda r: r.get("effective_from") or ""),
            "unpaid_days": to_str(unpaid),
            "pay_window": window.as_input(),
            "one_time": one_time,
            "ytd": {"opening_gross": to_str(openings[code].gross) if code in openings else "0",
                    "opening_tds": to_str(openings[code].tds) if code in openings else "0", "months": ytd_months},
            "arrears_basis": arrears_basis,
            "applied_changes": applied,
        })

    return {
        "period": str(period),
        "company": {"id": company.pk, "code": company.code, "name": company.name, "state": company.state,
                    **({"pay_day": company.pay_day} if company.pay_day else {})},
        "policy_versions": versions,
        "employees": employees,
        "knowledge_date": run.knowledge_date.isoformat(),
        "snapshot": {"id": run.snapshot_id, "hash": run.snapshot.content_hash},
        "changes": [ch.pk for ch in changes],
        "history_months": [r.period for r in finals],
    }
