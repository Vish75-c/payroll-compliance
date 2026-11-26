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
from .periods import Period
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
    return {
        "code": emp["code"],
        "name": emp.get("name", ""),
        "work_state": calc.state,
        "lines": [line.to_dict() for line in calc.lines],
        "amounts": {k: to_str(v) for k, v in calc.amounts.items()},
        "facts": facts(calc),
        "issues": calc.issues,
    }


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
    return {
        "engine_version": ENGINE_VERSION,
        "period": str(period),
        "company": company,
        "employee_count": len(results),
        "employees": results,
        "totals": {k: to_str(v) for k, v in sorted(totals.items())},
        "rules_used": sorted(used.values(), key=lambda r: (r["key"], r["jurisdiction"], r["effective_from"])),
        "issues": [{"employee": r["code"], **i} for r in results for i in r["issues"]],
    }


def _walk(lines):
    for line in lines:
        yield line
        yield from _walk(line.get("children", []))
