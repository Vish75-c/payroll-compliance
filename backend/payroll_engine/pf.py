"""Provident Fund: PF wages (with the Labour Codes add-back), the cap, and the employee's, company's and pension shares."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import policies as pol
from .earnings import Earnings
from .money import ZERO, d, inr, pct, round_amount


@dataclass
class PFResult:
    applicable: bool
    pf_wage: Decimal
    wage_from_components: Decimal
    addback: Decimal
    employee_base: Decimal
    employer_base: Decimal
    employee: Decimal
    employer: Decimal
    eps: Decimal
    epf_employer: Decimal
    rule: pol.Resolved
    policy: pol.Resolved
    wage_code: pol.Resolved | None
    wage_code_text: str = ""


def compute_pf(emp: dict, earn: Earnings, versions: list[dict]) -> PFResult:
    period = earn.period
    rule = pol.resolve_statutory(versions, "pf", period, "IN")
    policy = pol.resolve_company(versions, "pf_policy", period)
    wage_components = rule.payload["wage_components"]
    wage = sum((earn.earned.get(c, ZERO) for c in wage_components), ZERO)
    from_components = wage

    addback = ZERO
    wage_code_text = ""
    wage_code = pol.resolve(versions, "wage_definition", period, "IN")
    labour = pol.resolve_company(versions, "labour_codes", period)
    if wage_code and wage_code.payload.get("enabled") and labour.payload.get("apply") == "not_applied":
        # The company chose the older labour laws: the 50% rule is in force but not applied, and the step says so.
        wage_code = labour
        wage_code_text = "Labour Codes not applied (company setting: older labour laws), so nothing is added"
    elif wage_code and wage_code.payload.get("enabled") and "pf" in wage_code.payload.get("applies_to", ["pf"]):
        total = sum(earn.earned.values(), ZERO)
        code_wages = sum((earn.earned.get(c, ZERO) for c in wage_code.payload["wage_components"]), ZERO)
        exclusions = total - code_wages
        share = d(wage_code.payload["max_exclusion_share"])
        limit = total * share
        if exclusions > limit:
            addback = exclusions - limit
            wage += addback
            wage_code_text = (
                f"Labour Codes: parts of pay that are not wages ({inr(round_amount(exclusions))}) are more than "
                f"{pct(share)} of total pay ({inr(round_amount(limit))}), so the extra {inr(round_amount(addback))} counts as wages for PF"
            )
        else:
            wage_code_text = (
                f"Labour Codes: parts of pay that are not wages ({inr(round_amount(exclusions))}) are within "
                f"{pct(share)} of total pay, so nothing is added"
            )

    ceiling = d(rule.payload["wage_ceiling"])
    emp_base = min(wage, ceiling) if policy.get("wage_base") == "capped" else wage
    er_mode = policy.get("employer_wage_base", "same")
    if er_mode == "same":
        er_base = emp_base
    elif er_mode == "capped":
        er_base = min(wage, ceiling)
    else:
        er_base = wage
    method = rule.payload.get("rounding", "nearest")
    applicable = emp.get("pf_applicable", True) is not False
    if not applicable:
        employee = employer = eps = ZERO
    else:
        employee = round_amount(emp_base * d(rule.payload["employee_rate"]), method)
        employer = round_amount(er_base * d(rule.payload["employer_rate"]), method)
        eps_rate = d(rule.payload.get("eps_rate", 0))
        eps_ceiling = d(rule.payload.get("eps_wage_ceiling", rule.payload["wage_ceiling"]))
        eps = min(round_amount(min(wage, eps_ceiling) * eps_rate, method), employer) if eps_rate else ZERO
    return PFResult(
        applicable=applicable,
        pf_wage=wage,
        wage_from_components=from_components,
        addback=addback,
        employee_base=emp_base,
        employer_base=er_base,
        employee=employee,
        employer=employer,
        eps=eps,
        epf_employer=employer - eps,
        rule=rule,
        policy=policy,
        wage_code=wage_code,
        wage_code_text=wage_code_text,
    )
