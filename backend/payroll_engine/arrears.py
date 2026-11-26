"""Arrears: months already paid, worked out again with what is known now, each with its own rules."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from . import policies as pol
from .components import order_components
from .earnings import compute_earnings, contracted_rate, pay_dates_of
from .money import ZERO, d, inr, round_amount, to_str
from .periods import Period, parse_date
from .pf import compute_pf


@dataclass
class ArrearsMonth:
    period: Period
    components: dict[str, Decimal]
    total: Decimal
    pf_employee: Decimal
    pf_employer: Decimal
    explanation: str
    rate_before: Decimal
    rate_after: Decimal
    eps_employer: Decimal = ZERO


def compute_arrears(emp: dict, period: Period, versions: list[dict]) -> tuple[list[ArrearsMonth], pol.Resolved, list[dict]]:
    """Recalculate each already-paid month with the salary as known now and pay the difference.

    Each past month is recalculated with *its own* rules and settings (stored with that
    month), so arrears never re-open a rule decision that was made at the time.
    """
    policy = pol.resolve_company(versions, "arrears_policy", period)
    pf_policy = pol.resolve_company(versions, "pf_policy", period)
    if not policy.get("enabled", True):
        return [], policy, []
    lookback = int(policy.get("lookback_months", 12))
    out: list[ArrearsMonth] = []
    warnings: list[dict] = []
    for basis in sorted(emp.get("arrears_basis") or [], key=lambda b: b["period"]):
        past = Period.parse(basis["period"])
        if not (past < period) or past.months_until(period) > lookback:
            continue
        paid = basis["paid"]
        paid_pf_emp = d(paid.get("pf_employee"))
        paid_pf_er = d(paid.get("pf_employer"))
        past_emp = dict(emp)
        past_emp["date_of_joining"] = basis.get("date_of_joining", emp.get("date_of_joining"))
        past_emp["date_of_exit"] = basis.get("date_of_exit", emp.get("date_of_exit"))
        legacy_pf_applicable = bool(paid_pf_emp or paid_pf_er) or emp.get("pf_applicable", True)
        past_emp["pf_applicable"] = basis.get("pf_applicable", legacy_pf_applicable)
        past_versions = basis["policy_versions"]
        # The past month is worked out on its own dates (its pay group then), not this month's.
        past_emp.pop("pay_window", None)
        if basis.get("pay_window"):
            past_emp["pay_window"] = basis["pay_window"]
        rate, _, backfilled = contracted_rate(emp.get("compensation") or [], pay_dates_of(past_emp, past, past_versions).salary,
                                              parse_date(past_emp.get("date_of_joining")))
        if not rate or backfilled:
            # The salary for that month is not in the HR data, so it cannot be recalculated.
            # Paying "arrears" against a guessed salary would be wrong: skip and say so.
            warnings.append({"level": "warning", "message": f"No salary on record for {past.label}; arrears for that month were not "
                             "checked. If its salary changed, make sure the HR data includes the earlier salary."})
            continue
        earn = compute_earnings(past_emp, past, past_versions, basis.get("unpaid_days", "0"))
        paid_earned = {k: d(v) for k, v in (paid.get("earned") or {}).items()}
        deltas = {}
        for comp in order_components(set(earn.earned) | set(paid_earned)):
            delta = earn.earned.get(comp, ZERO) - paid_earned.get(comp, ZERO)
            if delta != 0:
                deltas[comp] = delta
        pf_emp = pf_er = eps_er = ZERO
        if pf_policy.get("arrears_contribution", "recompute_delta") == "recompute_delta":
            pf = compute_pf(past_emp, earn, past_versions)
            pf_emp = pf.employee - paid_pf_emp
            pf_er = pf.employer - paid_pf_er
            if "eps_employer" in paid:
                paid_eps = d(paid["eps_employer"])
            else:
                # Older inputs stored only the combined employer share and the earned components.
                paid_pf = compute_pf(past_emp, replace(earn, earned=paid_earned), past_versions)
                paid_eps = min(paid_pf.eps, paid_pf_er)
            eps_er = pf.eps - paid_eps
        new_gross = earn.regular_gross
        if "regular_gross" in paid:
            paid_gross = d(paid["regular_gross"])
        elif deltas:
            # Gross-only rounding can make the cash paid differ from the rounded component sum.
            method = pol.resolve_company(past_versions, "rounding", past).get("method", "nearest")
            paid_gross = round_amount(sum(paid_earned.values(), ZERO), method)
        else:
            paid_gross = new_gross
        total = new_gross - paid_gross
        if not (deltas or total or pf_emp or pf_er or eps_er):
            continue
        explanation = (
            f"{past.label} recalculated with the salary as known now: earned {inr(new_gross)} "
            f"instead of the {inr(paid_gross)} already paid"
        )
        late = d(basis.get("late_unpaid_days", "0"))
        if late:
            change = (f"{to_str(late)} more unpaid day{'s' if late != 1 else ''} reported after it was paid" if late > 0 else
                      f"{to_str(-late)} unpaid day{'s' if late != -1 else ''} fewer: counted by mistake when it was paid")
            explanation = (
                f"{past.label} worked out again with {change} ({to_str(earn.unpaid_days)} in all): earned "
                f"{inr(new_gross)} instead of the {inr(paid_gross)} already paid"
            )
        elif earn.unpaid_days:
            explanation += f" (same {to_str(earn.unpaid_days)} unpaid days as before)"
        out.append(
            ArrearsMonth(
                period=past,
                components=deltas,
                total=total,
                pf_employee=pf_emp,
                pf_employer=pf_er,
                explanation=explanation,
                rate_before=paid_gross,
                rate_after=new_gross,
                eps_employer=eps_er,
            )
        )
    return out, policy, warnings
