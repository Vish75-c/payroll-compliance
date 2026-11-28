"""One employee's month, as a list of steps.

Each step reads what the earlier steps worked out (``EmployeeCalc``) and adds its own lines (the amount with its
formula, reason and rules), amounts and issues. ``STEPS`` gives the order, which is also the order of the lines on
a payslip. A new statutory deduction or company contribution is a new step: it registers itself with
``add_deduction`` or ``add_employer_contribution`` and the totals include it, without changing the other steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import policies as pol
from .arrears import ArrearsMonth, compute_arrears
from .components import WAGE_LABELS, component_label, order_components, wage_for
from .earnings import Earnings, compute_earnings
from .explain import DEDUCTION, EARNING, EMPLOYER, INFO, TOTAL, Line
from .money import ZERO, d, inr, num, pct, round_amount, to_str
from .periods import Period
from .pf import PFResult, compute_pf
from .professional_tax import _slab_text, select_pt_slab
from .states import STATES, state_code
from .tds import compute_tds


@dataclass
class EmployeeCalc:
    emp: dict
    period: Period
    versions: list[dict]
    company: dict
    lines: list[Line] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    amounts: dict[str, Decimal] = field(default_factory=dict)
    # worked out by the steps, in order
    earn: Earnings | None = None
    arrears_months: list[ArrearsMonth] = field(default_factory=list)
    arrears_total: Decimal = ZERO
    arrears_pf_employee: Decimal = ZERO
    arrears_pf_employer: Decimal = ZERO
    arrears_eps_employer: Decimal = ZERO
    one_time_earnings: Decimal = ZERO
    one_time_taxable: Decimal = ZERO
    other_deductions: Decimal = ZERO
    deduction_lines: list[Line] = field(default_factory=list)
    gross: Decimal = ZERO
    pf: PFResult | None = None
    esi: dict = field(default_factory=dict)
    state: str = ""
    tds: Decimal = ZERO
    # what the totals add up: (name in the reason, amount), in payslip order
    deductions: list[tuple[str, Decimal]] = field(default_factory=list)
    employer_contributions: list[tuple[str, Decimal]] = field(default_factory=list)

    def add_deduction(self, name: str, amount: Decimal):
        self.deductions.append((name, amount))

    def add_employer_contribution(self, name: str, amount: Decimal):
        self.employer_contributions.append((name, amount))

    def money_text(self, value: Decimal) -> str:
        """Whole rupees, or paise when parts of pay are not rounded (rounding setting "gross only")."""
        two_dp = self.earn.rounding_mode == "gross_only"
        return inr(value, 2 if (two_dp and value != value.to_integral_value()) else None)

    def wage(self, choice: str) -> Decimal:
        return wage_for(choice, self.earn.regular_gross, self.arrears_total, self.one_time_earnings, self.earn.contracted_gross)


# --------------------------------------------------------------------------- regular pay

def regular_pay(c: EmployeeCalc):
    earn = compute_earnings(c.emp, c.period, c.versions, c.emp.get("unpaid_days", "0"))
    c.earn = earn
    c.issues += earn.issues
    method = pol.resolve_company(c.versions, "rounding", c.period).get("method", "nearest")
    comps = order_components(earn.rate)
    two_dp = earn.rounding_mode == "gross_only"
    money_text = c.money_text

    for comp in comps:
        rate_amount = earn.rate[comp]
        c.amounts[f"rate.{comp}"] = round_amount(rate_amount, method) if not two_dp else rate_amount
    c.amounts["fixed_gross"] = earn.contracted_gross
    c.amounts["days_in_month"] = Decimal(earn.days_in_month)
    c.amounts["unpaid_days"] = earn.lop_days
    c.amounts["not_employed_days"] = Decimal(earn.not_employed_days)
    if c.emp.get("pay_window"):
        c.amounts["basis_days"] = Decimal(earn.basis_days)

    lop_children = []
    divisor = to_str(earn.divisor)
    all_unpaid = earn.unpaid_days == earn.days_in_month
    for comp in comps:
        earned = earn.earned[comp]
        c.amounts[f"earned.{comp}"] = earned
        # A transition period (first month on a new pay cycle) pays only the days it covers.
        transition = bool(earn.basis_days) and earn.days_in_month != earn.basis_days
        monthly_text = money_text(c.amounts[f"rate.{comp}"])
        covered = f"{monthly_text} × {earn.days_in_month} ÷ {earn.basis_days} days" if transition else monthly_text
        cut = earn.cut[comp]
        if cut:
            formula = f"{covered} − {money_text(cut)} = {money_text(earned)}"
            reason = f"{component_label(comp)} for the month, less {to_str(earn.unpaid_days)} unpaid days."
            if earn.cut_by_segment and not all_unpaid:
                formula += " (days outside employment are each cut at the salary in force on that day in the prorated schedule)"
                reason += " Days outside employment use the nearest salary during employment."
            terms = []
            term_inputs = []
            for term in earn.cut_terms.get(comp, []):
                daily_rate = f"{money_text(term.monthly_rate)} ÷ {divisor}"
                if term.rounded_day_rate is not None:
                    daily_rate = f"{daily_rate}, rounded to {money_text(term.rounded_day_rate)}"
                terms.append(f"({daily_rate}) × {to_str(term.days)} {term.description}")
                term_inputs.append({"monthly_rate": term.monthly_rate, "days": term.days, "description": term.description,
                                    "rounded_day_rate": term.rounded_day_rate})
            cut_formula = " + ".join(terms)
            if all_unpaid:
                cut_formula = "Full monthly amount (no payable days)"
            elif comp in earn.capped_cuts:
                cut_formula = f"{cut_formula}, capped at the {'pay for the days covered' if transition else 'monthly amount'}"
            lop_children.append(
                Line(code=f"lop.{comp}", label=f"{component_label(comp)} cut", amount=cut, category=INFO,
                     formula=f"{cut_formula} → {money_text(cut)}", inputs={"terms": term_inputs, "divisor": earn.divisor})
            )
        else:
            formula = (f"{covered} = {money_text(earned)}" if transition
                       else money_text(earned) + (" (full month)" if not earn.unpaid_days else ""))
            reason = f"{component_label(comp)} for the month. {earn.rate_note.capitalize()}."
        c.lines.append(
            Line(
                code=f"earned.{comp}",
                label=component_label(comp),
                amount=earned,
                category=EARNING,
                formula=formula,
                reason=reason,
                rules=[earn.lop_ref, earn.rounding_ref, *earn.cycle_refs] if cut else earn.cycle_refs,
                inputs={"monthly_rate": earn.rate[comp], "unpaid_days": earn.unpaid_days, "divisor": earn.divisor},
            )
        )

    if earn.unpaid_days:
        parts = []
        if earn.lop_days:
            parts.append(f"{to_str(earn.lop_days)} unpaid leave days{earn.attendance_text}")
        if earn.not_employed_days:
            parts.append(f"{earn.not_employed_days} days before joining or after leaving")
        if all_unpaid:
            formula = f"Full monthly amount (no payable days) = {inr(earn.total_cut)}"
        elif two_dp:
            formula = f"Round the sum of the component cuts = {inr(earn.total_cut)}"
        else:
            cuts = " + ".join(inr(line.amount) for line in lop_children) or inr(ZERO)
            formula = f"{cuts} = {inr(earn.total_cut)}"
        reason = f"Cut for {' and '.join(parts)}. "
        if all_unpaid:
            reason += "No days are payable this month."
        else:
            reason = f"One day of pay is the monthly pay divided by {earn.divisor_text}. {reason}"
            reason += "Each part of pay is cut and rounded separately." if not two_dp else "Only the total cut is rounded."
        c.lines.append(
            Line(
                code="lop_deduction",
                label="Unpaid leave cut",
                amount=earn.total_cut,
                category=INFO,
                formula=formula,
                reason=reason,
                rules=[earn.lop_ref, earn.rounding_ref, *earn.cycle_refs],
                inputs={"unpaid_days": earn.unpaid_days, "divisor": earn.divisor, "monthly_pay": earn.contracted_gross},
                children=lop_children,
            )
        )
    c.amounts["lop_deduction"] = earn.total_cut


# --------------------------------------------------------------------------- arrears

def arrears(c: EmployeeCalc):
    months, policy, warnings = compute_arrears(c.emp, c.period, c.versions)
    c.issues += warnings
    c.arrears_months = months
    c.arrears_total = total = sum((a.total for a in months), ZERO)
    c.arrears_pf_employee = sum((a.pf_employee for a in months), ZERO)
    c.arrears_pf_employer = sum((a.pf_employer for a in months), ZERO)
    c.arrears_eps_employer = sum((a.eps_employer for a in months), ZERO)
    # Keep component deltas in the history even when they do not result in a payment.
    months = [a for a in months if a.total or a.pf_employee or a.pf_employer or a.eps_employer]
    if months:
        children = []
        for a in months:
            comp_text = ", ".join(f"{component_label(k)} {c.money_text(v)}" for k, v in a.components.items())
            children.append(
                Line(
                    code=f"arrears.{a.period}",
                    label=f"Salary for {a.period.label} (was on hold)" if a.held else f"Arrears for {a.period.label}",
                    amount=a.total,
                    category=INFO,
                    formula=f"{inr(a.rate_after)} − {inr(a.rate_before)} already paid = {inr(a.total)}",
                    reason=a.explanation + (f" ({comp_text})" if comp_text else ""),
                    inputs={"components": a.components, "pf_employee": a.pf_employee, "pf_employer": a.pf_employer,
                            "eps_employer": a.eps_employer},
                )
            )
        months_txt = ", ".join(a.period.label.split()[0] for a in months)
        late_days = any("reported after it was paid" in a.explanation or "counted by mistake" in a.explanation for a in months)
        held = [a for a in months if a.held]
        c.lines.append(
            Line(
                code="arrears",
                label="Arrears (back pay)" if total >= 0 else "Recovery for earlier months",
                amount=total,
                category=EARNING,
                formula=" + ".join(inr(a.total) for a in months) + f" = {inr(total)}",
                reason=(f"Salary for {', '.join(a.period.label for a in held)}, held then for missing HR data, is paid now"
                        + (" with the other months' revisions." if len(held) < len(months) else ".")
                        if held else
                        f"{months_txt} {'was' if len(months) == 1 else 'were'} worked out again after being paid "
                        "(a salary change or unpaid days reported late); the difference is paid now, or taken back if it is negative."
                        if late_days or total < 0 else
                        f"Salary for {months_txt} was revised after it was paid. Each month is worked out again with the revised salary and the difference is paid now."),
                rules=[policy.ref],
                children=children,
            )
        )
    c.amounts["arrears"] = total


# --------------------------------------------------------------------------- one-time items

def one_time_items(c: EmployeeCalc):
    for item in sorted(c.emp.get("one_time") or [], key=lambda i: (i.get("kind", ""), i.get("code", ""), i.get("ref", ""))):
        amount = d(item["amount"])
        code = item.get("code") or "other"
        label = item.get("label") or component_label(code)
        source = item.get("source_text")
        reason = "One-time item confirmed by the payroll maker"
        if source:
            reason += f' from the instruction "{source}"'
        if item.get("kind") == "deduction":
            c.other_deductions += amount
            c.amounts[f"deduction.{code}"] = c.amounts.get(f"deduction.{code}", ZERO) + amount
            # shown after the statutory deductions (see other_deductions)
            c.deduction_lines.append(
                Line(code=f"deduction.{code}", label=label, amount=amount, category=DEDUCTION, formula=inr(amount), reason=reason,
                     inputs={"ref": item.get("ref", "")})
            )
        else:
            c.one_time_earnings += amount
            if item.get("taxable", True):
                c.one_time_taxable += amount
            c.amounts[f"one_time.{code}"] = c.amounts.get(f"one_time.{code}", ZERO) + amount
            c.lines.append(
                Line(code=f"one_time.{code}", label=label, amount=amount, category=EARNING, formula=inr(amount),
                     reason=reason + ("" if item.get("taxable", True) else " (not taxable)"), inputs={"ref": item.get("ref", "")})
            )


def gross_pay(c: EmployeeCalc):
    regular = c.earn.regular_gross
    c.gross = gross = regular + c.arrears_total + c.one_time_earnings
    parts = [f"{inr(regular)} regular pay"]
    if c.arrears_total:
        parts.append(f"{inr(c.arrears_total)} arrears")
    if c.one_time_earnings:
        parts.append(f"{inr(c.one_time_earnings)} one-time")
    c.lines.append(
        Line(
            code="gross",
            label="Gross pay",
            amount=gross,
            category=TOTAL,
            formula=" + ".join(parts) + f" = {inr(gross)}" if len(parts) > 1 else f"{inr(gross)}",
            reason="Everything earned this month before deductions",
        )
    )
    c.amounts["regular_gross"] = regular
    c.amounts["gross"] = gross


# --------------------------------------------------------------------------- deductions

def pf_employee(c: EmployeeCalc):
    earn, money_text = c.earn, c.money_text
    pf = c.pf = compute_pf(c.emp, earn, c.versions)
    pf_rate = d(pf.rule.payload["employee_rate"])
    ceiling = d(pf.rule.payload["wage_ceiling"])
    if not pf.applicable:
        formula = "Not applicable"
        reason = "This employee is not a PF member this month (marked in HR data)."
    elif pf.policy.get("wage_base") == "capped":
        capped = pf.pf_wage > ceiling
        formula = (
            f"{pct(pf_rate)} of {inr(pf.employee_base, 2 if pf.employee_base != pf.employee_base.to_integral_value() else None)}"
            + (f" ({inr(ceiling)} cap)" if capped else "")
            + f" = {inr(pf.employee)}"
        )
        reason = (
            f"PF is worked out on the first {inr(ceiling)} of PF wages (company setting). "
            + (f"PF wages this month are {money_text(pf.pf_wage)}, above the cap, so the cap is used." if capped
               else f"PF wages this month are {money_text(pf.pf_wage)}, below the cap, so all of it is used.")
        )
    else:
        formula = f"{pct(pf_rate)} of {money_text(pf.employee_base)} = {inr(pf.employee)}"
        reason = f"PF is worked out on all of the PF wages (company setting): {money_text(pf.pf_wage)}."
    if pf.wage_code_text:
        reason += " " + pf.wage_code_text + "."
    wage_parts = " + ".join(
        f"{component_label(k)} {money_text(earn.earned.get(k, ZERO))}" for k in pf.rule.payload["wage_components"] if k in earn.earned
    ) or "no PF wage components"
    children = [
        Line(code="pf_wage", label="PF wages", amount=pf.pf_wage, category=INFO,
             formula=wage_parts + (f" + Labour Codes add-back {money_text(pf.addback)}" if pf.addback else "") + f" = {money_text(pf.pf_wage)}",
             reason="PF wages are the parts of pay listed in the PF rule, after unpaid leave",
             rules=[pf.rule.ref] + ([pf.wage_code.ref] if pf.wage_code else []))
    ]
    on_arrears = c.arrears_pf_employee
    total = pf.employee + on_arrears
    if on_arrears:
        children.append(Line(code="pf_employee.current", label="PF on this month's pay", amount=pf.employee, category=INFO, formula=formula))
        children.append(
            Line(code="pf_employee.arrears", label="PF on arrears", amount=on_arrears, category=INFO,
                 formula=" + ".join(f"{a.period.short_label}: {inr(a.pf_employee)}" for a in c.arrears_months if a.pf_employee) + f" = {inr(on_arrears)}",
                 reason="PF for the revised months worked out again with the rules of those months, minus PF already paid")
        )
        formula = f"{inr(pf.employee)} this month + {inr(on_arrears)} on arrears = {inr(total)}"
    elif c.arrears_months and pf.applicable:
        reason += " The arrears add no PF: PF for the revised months comes out the same as what was already paid."
    c.lines.append(
        Line(code="pf_employee", label="PF (employee 12%)", amount=total, category=DEDUCTION,
             formula=formula, reason=reason, rules=[pf.rule.ref, pf.policy.ref] + ([pf.wage_code.ref] if pf.wage_code and pf.addback else []),
             inputs={"pf_wage": pf.pf_wage, "base": pf.employee_base, "rate": pf_rate, "ceiling": ceiling},
             children=children)
    )
    c.amounts["pf_wage"] = pf.pf_wage
    c.amounts["pf_employee"] = total
    c.add_deduction("PF", total)


def esi_employee(c: EmployeeCalc):
    rule = pol.resolve_statutory(c.versions, "esi", c.period, "IN")
    policy = pol.resolve_company(c.versions, "esi_policy", c.period)
    threshold = d(rule.payload["threshold"])
    elig_choice = policy.get("eligibility_wage")
    elig_wage = c.wage(elig_choice)
    esi_wage = c.wage(policy.get("contribution_wage"))
    method = rule.payload.get("rounding", "up")
    applies = c.emp.get("esi_applicable", True) is not False
    period_start = c.period.esi_contribution_start
    continuing = applies and any(
        period_start <= p < c.period for p in map(Period.parse, c.emp.get("covered_esi_months") or ())
    )
    qualifies = ZERO < elig_wage <= threshold and esi_wage > ZERO
    covered = applies and (continuing or qualifies)
    contribution_wage = esi_wage if covered and esi_wage > ZERO else ZERO

    if not applies:
        reason = "This employee is outside ESI coverage as marked in HR data."
    elif covered and not contribution_wage:
        reason = "ESI coverage continues with no contribution because no ESI wages are payable this month."
    elif continuing and not qualifies:
        reason = f"ESI coverage continues for the contribution period that began in {period_start.label}."
    elif qualifies:
        reason = f"ESI applies because {WAGE_LABELS[elig_choice]} ({inr(elig_wage)}) is {inr(threshold)} or less."
    elif elig_wage > threshold:
        reason = f"ESI does not apply because {WAGE_LABELS[elig_choice]} ({inr(elig_wage)}) is above {inr(threshold)}."
    else:
        reason = "Establishing ESI coverage requires positive wages for both eligibility and contributions."

    employee_rate = d(rule.payload["employee_rate"])
    employee_unrounded = contribution_wage * employee_rate
    employee = round_amount(employee_unrounded, method)
    inputs = {"eligibility_wage": elig_wage, "esi_wage": esi_wage, "threshold": threshold, "covered": covered}
    formula = (f"{pct(employee_rate)} of {inr(contribution_wage)} = {num(employee_unrounded)}, rounded {method} = {inr(employee)}"
               if contribution_wage else "₹0 due")
    exemption = rule.payload.get("employee_daily_wage_exemption")
    if contribution_wage and exemption is not None:
        # ESI (Central) Rules 2(1B) and 52 use 26 for monthly-rated average daily wages.
        supplied_daily_wage = c.emp.get("esi_average_daily_wage")
        daily_wage = d(supplied_daily_wage) if supplied_daily_wage not in (None, "") else c.earn.contracted_gross / 26
        exemption_limit = d(exemption)
        inputs.update(average_daily_wage=daily_wage, employee_daily_wage_exemption=exemption_limit)
        if daily_wage <= exemption_limit:
            employee = ZERO
            formula = f"Average daily wage of {inr(daily_wage)} qualifies for the {inr(exemption_limit)} exemption. Employee contribution is ₹0."

    c.lines.append(
        Line(code="esi_employee", label=f"ESI (employee {pct(employee_rate)})", amount=employee, category=DEDUCTION,
             formula=formula, reason=reason, rules=[rule.ref, policy.ref], inputs=inputs)
    )
    c.amounts["esi_wage"] = contribution_wage
    c.amounts["esi_employee"] = employee
    c.esi = {"rule": rule, "policy": policy, "wage": contribution_wage, "covered": covered, "reason": reason}
    c.add_deduction("ESI", employee)


def professional_tax(c: EmployeeCalc):
    period, versions = c.period, c.versions
    # By law PT follows where the employee works; the company's state (from onboarding) is used when that is missing.
    own_state = state_code(c.emp.get("work_state"))
    state = c.state = own_state or state_code(c.company.get("state"))
    state_name = STATES.get(state, state)
    whose = "the employee's work location" if own_state else "the company's state, as the employee has no work location"
    policy = pol.resolve_company(versions, "pt_policy", period)
    basis_choice = policy.get("basis")
    basis = c.wage(basis_choice)
    rule = pol.resolve(versions, "professional_tax", period, f"IN-{state}") if state else None
    rules = [policy.ref]
    if rule is None:
        pt = ZERO
        formula = "Not applicable"
        reason = f"No professional tax rule is stored for {state or 'this employee’s state'} on {period.start.isoformat()}."
        if state:
            c.issues.append({"level": "info", "message": f"No professional tax rule for state {state}; PT is 0."})
    elif rule.payload.get("levied") is False:
        rules.insert(0, rule.ref)
        pt = ZERO
        formula = "Not charged in this state"
        reason = f"{state_name} does not charge professional tax ({whose})."
    else:
        rules.insert(0, rule.ref)
        slab = select_pt_slab(rule.payload["slabs"], basis, period.month, c.emp.get("gender"))
        if slab is None:
            pt = ZERO
            formula = "No slab matches"
            reason = f"No slab of the {state} rule matches {inr(basis)}"
            if any(s.get("gender") for s in rule.payload["slabs"]) and not c.emp.get("gender"):
                c.issues.append({"level": "error", "message": f"Professional tax in {state} depends on gender, but gender is missing. "
                                 "Add it in the HR tool and read again."})
        else:
            if "rate" in slab:
                pt = round_amount(basis * d(slab["rate"]), "nearest")
                formula = f"{pct(d(slab['rate']))} of {inr(basis)} = {inr(pt)}"
            else:
                pt = d(slab["amount"])
                formula = f"{inr(pt)} (slab: {_slab_text(slab)})"
            reason = (f"{state} professional tax on {WAGE_LABELS[basis_choice]} of {inr(basis)}: the slab for {_slab_text(slab)} applies. "
                      f"{state_name} is {whose}.")
    c.lines.append(
        Line(code="pt", label="Professional tax", amount=pt, category=DEDUCTION, formula=formula, reason=reason, rules=rules,
             inputs={"basis": basis, "state": state})
    )
    c.amounts["pt_basis"] = basis
    c.amounts["pt"] = pt
    c.add_deduction("professional tax", pt)


def income_tax(c: EmployeeCalc):
    line, amounts = compute_tds(c.emp, c.period, c.versions, c.gross, c.one_time_earnings - c.one_time_taxable, c.one_time_taxable)
    c.lines.append(line)
    c.amounts.update(amounts)
    c.tds = amounts["tds"]
    c.add_deduction("TDS", c.tds)


def other_deductions(c: EmployeeCalc):
    """One-time deductions (advance or loan recovery…) come after the statutory ones."""
    c.lines.extend(c.deduction_lines)
    if c.other_deductions:
        c.add_deduction("other deductions", c.other_deductions)


def net_pay(c: EmployeeCalc):
    total = sum((amount for _, amount in c.deductions), ZERO)
    c.lines.append(
        Line(code="total_deductions", label="Total deductions", amount=total, category=TOTAL,
             formula=" + ".join(inr(amount) for _, amount in c.deductions) + f" = {inr(total)}",
             reason=" + ".join(name for name, _ in c.deductions))
    )
    net = c.gross - total
    c.lines.append(
        Line(code="net_pay", label="Net pay", amount=net, category=TOTAL,
             formula=f"{inr(c.gross)} − {inr(total)} = {inr(net)}",
             reason="Gross pay minus deductions: what reaches the employee's bank account")
    )
    c.amounts["total_deductions"] = total
    c.amounts["net_pay"] = net
    if net < 0:
        c.issues.append({"level": "error", "message": f"Net pay is negative ({inr(net)}). Check deductions before approving."})


# --------------------------------------------------------------------------- paid by the company on top

def pf_employer(c: EmployeeCalc):
    pf = c.pf
    on_arrears = c.arrears_pf_employer
    eps_on_arrears = c.arrears_eps_employer
    total = pf.employer + on_arrears
    eps = pf.eps + eps_on_arrears
    epf = total - eps
    eps_rate = d(pf.rule.payload.get("eps_rate", 0))
    ceiling = d(pf.rule.payload["wage_ceiling"])
    formula = (f"{pct(d(pf.rule.payload['employer_rate']))} of {c.money_text(pf.employer_base)} = {inr(pf.employer)}"
               if pf.applicable else "Not applicable this month")
    if on_arrears:
        formula = f"{inr(pf.employer)} this month + {inr(on_arrears)} on arrears = {inr(total)}"
    reason = (f"Paid by the company on top of salary. Of this, {inr(eps)} goes to the pension scheme "
              f"({pct(eps_rate)} of PF wages up to {inr(d(pf.rule.payload.get('eps_wage_ceiling', ceiling)))}) and the rest to the PF account.")
    if on_arrears or eps_on_arrears:
        reason = ("The company's share includes adjustments for earlier months. "
                  f"{inr(eps)} goes to the pension scheme and {inr(epf)} goes to the PF account.")
    c.lines.append(
        Line(code="pf_employer", label="PF (company 12%)", amount=total, category=EMPLOYER,
             formula=formula, reason=reason,
             rules=[pf.rule.ref, pf.policy.ref],
             children=[
                 Line(code="eps_employer", label="Pension (EPS)", amount=eps, category=INFO),
                 Line(code="epf_employer", label="PF account (EPF)", amount=epf, category=INFO),
             ])
    )
    c.amounts["pf_employer"] = total
    c.amounts["eps_employer"] = eps
    c.amounts["epf_employer"] = epf
    c.add_employer_contribution("PF", total)


def esi_employer(c: EmployeeCalc):
    esi = c.esi
    rule, wage = esi["rule"], esi["wage"]
    rate = d(rule.payload["employer_rate"])
    method = rule.payload.get("rounding", "up")
    unrounded = wage * rate
    employer = round_amount(unrounded, method)
    c.lines.append(
        Line(code="esi_employer", label=f"ESI (company {pct(rate)})", amount=employer, category=EMPLOYER,
             formula=f"{pct(rate)} of {inr(wage)} = {num(unrounded)}, rounded {method} = {inr(employer)}" if wage else "₹0 due",
             reason="Paid by the company on top of salary" if wage else esi["reason"], rules=[rule.ref, esi["policy"].ref])
    )
    c.amounts["esi_employer"] = employer
    c.add_employer_contribution("ESI", employer)


def cost_to_company(c: EmployeeCalc):
    cost = c.gross + sum((amount for _, amount in c.employer_contributions), ZERO)
    names = [name for name, _ in c.employer_contributions]
    c.lines.append(
        Line(code="employer_cost", label="Cost to company this month", amount=cost, category=TOTAL,
             formula=" + ".join([inr(c.gross)] + [inr(amount) for _, amount in c.employer_contributions]) + f" = {inr(cost)}",
             reason="Gross pay plus the company's own " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)))
    )
    c.amounts["employer_cost"] = cost


STEPS = (
    regular_pay,
    arrears,
    one_time_items,
    gross_pay,
    pf_employee,
    esi_employee,
    professional_tax,
    income_tax,
    other_deductions,
    net_pay,
    pf_employer,
    esi_employer,
    cost_to_company,
)


def facts(c: EmployeeCalc) -> dict:
    """Facts carried forward for arrears, ESI coverage and year-to-date tax."""
    pf = c.pf
    return {
        "earned": {k: to_str(v) for k, v in c.earn.earned.items()},
        "regular_gross": to_str(c.earn.regular_gross),
        "pf_employee_regular": to_str(pf.employee),
        "pf_employer_regular": to_str(pf.employer),
        "eps_employer_regular": to_str(pf.eps),
        "esi_covered": c.esi.get("covered", False),
        "arrears_detail": [
            {"period": str(a.period), "components": {k: to_str(v) for k, v in a.components.items()}, "total": to_str(a.total),
             "pf_employee": to_str(a.pf_employee), "pf_employer": to_str(a.pf_employer), "eps_employer": to_str(a.eps_employer),
             **({"held": True} if a.held else {})}
            for a in c.arrears_months
        ],
        "gross": to_str(c.gross),
        "taxable_gross": to_str(c.gross - (c.one_time_earnings - c.one_time_taxable)),
        "tds": to_str(c.tds),
        "unpaid_days": to_str(c.earn.lop_days),
        "contracted_gross": to_str(c.earn.contracted_gross),
    }
