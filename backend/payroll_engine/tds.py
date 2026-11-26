"""TDS: estimate the year's pay, work out the year's tax, and spread what is left over the months left."""
from __future__ import annotations

from . import policies as pol
from .earnings import compute_earnings
from .explain import DEDUCTION, INFO, Line
from .money import ZERO, d, inr, round_amount
from .periods import Period, parse_date
from .tax import annual_tax


def compute_tds(emp, period, versions, gross, non_taxable, one_time_taxable):
    rule = pol.resolve_statutory(versions, "income_tax", period, "IN")
    policy = pol.resolve_company(versions, "tds_policy", period)
    unit = policy.get("annual_tax_rounding_unit", 1)
    monthly_method = policy.get("monthly_rounding", "nearest")

    ytd = emp.get("ytd") or {}
    months = [m for m in ytd.get("months", []) if Period.parse(m["period"]).same_fy(period) and Period.parse(m["period"]) < period]
    paid_gross = d(ytd.get("opening_gross")) + sum((d(m.get("taxable_gross", m["gross"])) for m in months), ZERO)
    tds_paid = d(ytd.get("opening_tds")) + sum((d(m["tds"]) for m in months), ZERO)

    doe = parse_date(emp.get("date_of_exit"))
    last = period.fy_last
    if doe and Period.of(doe) < last:
        last = max(Period.of(doe), period)
    future_pay = []
    future_rules = {}
    p = period.next()
    while p <= last:
        # Month-specific settings (including draft what-ifs) apply only to the month they were set for,
        # and this month's pay window (its pay group dates) does not carry into later months.
        future_versions = [
            v for v in versions
            if v.get("scope") not in (pol.MONTH, pol.OVERRIDE) or parse_date(v["effective_from"]) == p.start
        ]
        future_emp = {k: v for k, v in emp.items() if k != "pay_window"}
        earnings = compute_earnings(future_emp, p, future_versions, ZERO)
        future_pay.append(earnings.regular_gross)
        rounding_ref = earnings.rounding_ref
        future_rules[rounding_ref.key, rounding_ref.version_id] = rounding_ref
        if earnings.not_employed_days:
            lop_ref = earnings.lop_ref
            future_rules[lop_ref.key, lop_ref.version_id] = lop_ref
        p = p.next()
    future_total = sum(future_pay, ZERO)
    months_left = period.months_until(last) + 1

    current_taxable = gross - non_taxable
    projected = paid_gross + current_taxable + future_total
    std = d(rule.payload["standard_deduction"])
    taxable = max(ZERO, projected - std)
    trace = annual_tax(taxable, rule.payload, unit)
    remaining = max(ZERO, trace.total - tds_paid)

    tax_in_month = policy.get("one_time_income") == "tax_in_month" and one_time_taxable != ZERO
    if months_left == 1:
        tds = remaining
        formula = f"Remaining annual tax in the final month = {inr(tds)}"
    elif tax_in_month:
        base_trace = annual_tax(max(ZERO, projected - one_time_taxable - std), rule.payload, unit)
        base_remaining = max(ZERO, base_trace.total - tds_paid)
        regular = round_amount(base_remaining / months_left, monthly_method)
        extra = remaining - base_remaining
        tds = min(remaining, max(ZERO, regular + extra))
        formula = (f"{inr(base_remaining)} tax remaining without this month's one-time pay ÷ {months_left} months "
                   f"= {inr(regular)}. One-time pay adjusts this by {inr(extra)}. "
                   f"After the adjustment and within the remaining annual tax of {inr(remaining)}, withhold {inr(tds)}.")
    else:
        tds = min(remaining, round_amount(remaining / months_left, monthly_method))
        formula = f"{inr(remaining)} remaining tax over {months_left} months gives {inr(tds)} this month after rounding"
        if trace.total == 0 and tds_paid == 0:
            formula = "Tax for the year is ₹0, so ₹0 this month"
        elif tds_paid > trace.total:
            formula = f"Tax already deducted ({inr(tds_paid)}) covers the year's tax ({inr(trace.total)}), so ₹0"

    future_text = ""
    if future_pay:
        if len(set(future_pay)) == 1:
            future_text = f"{len(future_pay)} more months at {inr(future_pay[0])} = {inr(future_total)}"
        else:
            future_text = f"earnings over {len(future_pay)} more months = {inr(future_total)}"
    projection_parts = [f"{inr(paid_gross)} paid so far this year", f"{inr(current_taxable)} this month"]
    if future_text:
        projection_parts.append(future_text)
    children = [
        Line(code="tds.projection", label="Estimated pay for the year", amount=projected, category=INFO,
             formula=" + ".join(projection_parts) + f" = {inr(projected)}",
             reason=f"{period.fy_label}: pay already paid, plus this month, plus estimated earnings for the months left",
             rules=list(future_rules.values())),
        Line(code="tds.taxable", label="Taxable income", amount=taxable, category=INFO,
             formula=f"Greater of ₹0 and ({inr(projected)} − {inr(std)} standard deduction) = {inr(taxable)}"),
        Line(code="tds.annual_tax", label="Tax for the year", amount=trace.total, category=INFO,
             formula="; ".join(trace.lines()), reason=f"{rule.payload.get('regime', 'new').capitalize()} tax regime slabs from the stored rule"),
        Line(code="tds.paid", label="Tax already deducted this year", amount=tds_paid, category=INFO),
    ]
    if trace.total == 0:
        reason = f"No TDS: estimated taxable income for the year is {inr(taxable)}, and the tax on it is ₹0 under the {rule.payload.get('regime', 'new')} regime."
    elif remaining == ZERO:
        reason = "Tax already deducted covers the estimated tax for the year."
    elif months_left == 1:
        reason = "Withhold the exact remaining annual liability in the last projected month."
    elif tax_in_month:
        reason = "Spread the remaining tax on regular pay over the months left and apply the tax adjustment on one-time pay this month."
    else:
        reason = "Estimate the year's pay, work out the year's tax, subtract the tax already taken, and spread the rest over the months left."
    line = Line(code="tds", label="Income tax (TDS)", amount=tds, category=DEDUCTION, formula=formula, reason=reason,
                rules=[rule.ref, policy.ref], children=children,
                inputs={"projected_income": projected, "taxable_income": taxable, "annual_tax": trace.total,
                        "tds_paid": tds_paid, "months_left": months_left})
    return line, {
        "tds": tds,
        "tds_projected_income": projected,
        "tds_taxable_income": taxable,
        "tds_annual_tax": trace.total,
        "tds_paid_before": tds_paid,
    }
