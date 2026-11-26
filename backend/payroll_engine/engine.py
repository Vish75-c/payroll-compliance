"""The payroll calculation: one month for every employee, from a plain input to a result with every line explained.

Each part of pay has its own module (``earnings``, ``arrears``, ``pf``, ``professional_tax``, ``tds``); ``steps``
puts them together for one employee in payslip order. This module runs the steps for each employee and adds up
the month. Nothing here reads a database, the clock or an AI model: the same input always gives the same result.
"""
from __future__ import annotations

from decimal import Decimal

from . import policies as pol
from .arrears import ArrearsMonth, compute_arrears
from .components import COMPONENT_LABELS, COMPONENT_ORDER, WAGE_LABELS, CalculationError, component_label, order_components
from .earnings import Earnings, compute_earnings, contracted_rate
from .money import ZERO, d, to_str
from .periods import Period, pay_date, parse_date
from .pf import PFResult, compute_pf
from .professional_tax import select_pt_slab
from .steps import STEPS, EmployeeCalc, facts
from .tds import compute_tds

# Other code imports these from the engine.
__all__ = [
    "ENGINE_VERSION", "TOTAL_KEYS", "calculate_month", "calculate_employee", "CalculationError",
    "ArrearsMonth", "compute_arrears", "COMPONENT_LABELS", "COMPONENT_ORDER", "WAGE_LABELS", "component_label", "order_components",
    "Earnings", "compute_earnings", "contracted_rate", "PFResult", "compute_pf", "select_pt_slab", "compute_tds",
]

ENGINE_VERSION = "1.1.0"


def calculate_employee(emp: dict, period: Period, versions: list[dict], company: dict, steps=STEPS) -> dict:
    calc = EmployeeCalc(emp=emp, period=period, versions=versions, company=company)
    for step in steps:
        step(calc)
    out = {
        "code": emp["code"],
        "name": emp.get("name", ""),
        "work_state": calc.state,
        "lines": [line.to_dict() for line in calc.lines],
        "amounts": {k: to_str(v) for k, v in calc.amounts.items()},
        "facts": facts(calc),
        "issues": calc.issues,
    }
    if emp.get("pay_window"):
        out["pay_window"] = emp["pay_window"]  # the person's pay group and dates, for the register and the files
    return out


# =========================================================================== month

TOTAL_KEYS = [
    "fixed_gross", "lop_deduction", "arrears", "regular_gross", "gross", "pf_employee", "esi_employee", "pt", "tds",
    "total_deductions", "net_pay", "pf_employer", "eps_employer", "epf_employer", "esi_employer", "employer_cost",
]


def calculate_month(month_input: dict) -> dict:
    period = Period.parse(month_input["period"])
    versions = month_input.get("policy_versions") or []
    company = month_input.get("company") or {}
    employees = sorted(month_input.get("employees") or [], key=lambda e: e["code"])
    seen = set()
    results = []
    for emp in employees:
        if emp["code"] in seen:
            raise CalculationError(f"employee code {emp['code']} appears twice")
        seen.add(emp["code"])
        try:
            results.append(calculate_employee(emp, period, versions, company))
        except pol.PolicyMissing as exc:
            raise CalculationError(str(exc)) from exc

    totals: dict[str, Decimal] = {}
    for r in results:
        for key, value in r["amounts"].items():
            if key in TOTAL_KEYS or key.startswith(("one_time.", "deduction.")):
                totals[key] = totals.get(key, ZERO) + d(value)
    used: dict[str, dict] = {}
    for r in results:
        for line in _walk(r["lines"]):
            for ref in line["rules"]:
                used[f"{ref['key']}|{ref['jurisdiction']}|{ref['version_id']}"] = ref
    out = {
        "engine_version": ENGINE_VERSION,
        "period": str(period),
        "company": company,
        "employee_count": len(results),
        "employees": results,
        "totals": {k: to_str(v) for k, v in sorted(totals.items())},
        "rules_used": sorted(used.values(), key=lambda r: (r["key"], r["jurisdiction"], r["effective_from"])),
        "issues": [{"employee": r["code"], **i} for r in results for i in r["issues"]],
    }
    if any(r.get("pay_window") for r in results):
        out["totals_by_group"] = totals_by_group(results)
    return out


def totals_by_group(results: list[dict]) -> list[dict]:
    """The month split by pay group: people, dates, pay date and totals of each group (default group first)."""
    groups: dict[str, dict] = {}
    for r in results:
        pw = r.get("pay_window") or {}
        g = groups.setdefault(pw.get("group", ""), {
            "group": pw.get("group", ""), "name": pw.get("group_name", ""), "default": bool(pw.get("default")),
            "salary_from": pw.get("salary_from"), "salary_to": pw.get("salary_to"), "people": 0, "transition": 0,
            "pay_date": pay_date(parse_date(pw["salary_to"]), int(pw["pay_day"])).isoformat() if pw.get("pay_day") else "",
            "totals": {}})
        g["people"] += 1
        g["transition"] += 1 if pw.get("transition") else 0
        if pw.get("salary_from") and pw["salary_from"] < (g["salary_from"] or pw["salary_from"]):
            g["salary_from"] = pw["salary_from"]  # a transition starts earlier or later than the group's normal cycle
        for key, value in r["amounts"].items():
            if key in TOTAL_KEYS:
                g["totals"][key] = g["totals"].get(key, ZERO) + d(value)
    out = sorted(groups.values(), key=lambda g: (not g["default"], g["name"]))
    for g in out:
        g["totals"] = {k: to_str(v) for k, v in sorted(g["totals"].items())}
    return out


def _walk(lines):
    for line in lines:
        yield line
        yield from _walk(line.get("children", []))
