"""Annual income tax from a stored income-tax rule. Pure arithmetic with a step-by-step trace."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .money import ZERO, d, inr, pct, round_amount


@dataclass
class TaxTrace:
    taxable: Decimal
    slab_steps: list[str] = field(default_factory=list)
    slab_tax: Decimal = ZERO
    rebate: Decimal = ZERO
    rebate_note: str = ""
    surcharge: Decimal = ZERO
    surcharge_note: str = ""
    cess: Decimal = ZERO
    total: Decimal = ZERO

    def lines(self) -> list[str]:
        out = [f"Taxable income {inr(self.taxable)}"]
        out += self.slab_steps
        out.append(f"Tax by slabs = {inr(self.slab_tax)}")
        if self.rebate:
            out.append(f"Less rebate {inr(self.rebate)} ({self.rebate_note})")
        if self.surcharge:
            out.append(f"Add surcharge {inr(self.surcharge)} ({self.surcharge_note})")
        if self.cess:
            out.append(f"Add cess {inr(self.cess)}")
        out.append(f"Tax for the year = {inr(self.total)}")
        return out


def _slab_tax(taxable: Decimal, slabs: list[dict], steps: list[str] | None = None) -> Decimal:
    tax = ZERO
    lower = ZERO
    for slab in slabs:
        upper = None if slab.get("upto") is None else d(slab["upto"])
        rate = d(slab["rate"])
        if taxable <= lower:
            break
        top = taxable if upper is None else min(taxable, upper)
        portion = top - lower
        if portion > 0:
            amount = portion * rate
            tax += amount
            if steps is not None:
                upper_txt = inr(top)
                steps.append(f"{inr(lower)} to {upper_txt}: {pct(rate)} of {inr(portion)} = {inr(round_amount(amount))}")
        if upper is None:
            break
        lower = upper
    return tax


def _tax_before_cess(taxable: Decimal, rule: dict, trace: TaxTrace | None = None) -> tuple[Decimal, Decimal]:
    """Return (tax after rebate, surcharge)."""
    slabs = rule["slabs"]
    steps = trace.slab_steps if trace else None
    tax = _slab_tax(taxable, slabs, steps)
    if trace:
        trace.slab_tax = tax

    rebate_rule = rule.get("rebate")
    if rebate_rule:
        limit = d(rebate_rule["income_upto"])
        max_rebate = d(rebate_rule["max_rebate"])
        if taxable <= limit:
            rebate = min(tax, max_rebate)
            if trace and rebate:
                trace.rebate = rebate
                trace.rebate_note = f"no tax when taxable income is {inr(limit)} or less"
            tax -= rebate
        elif rebate_rule.get("marginal_relief", False) and tax > taxable - limit:
            relief = tax - (taxable - limit)
            if trace:
                trace.rebate = relief
                trace.rebate_note = f"marginal relief: tax cannot exceed income above {inr(limit)}"
            tax -= relief

    surcharge = ZERO
    rows = sorted(rule.get("surcharge") or [], key=lambda r: d(r["above"]))
    applicable = [r for r in rows if taxable > d(r["above"])]
    if applicable and tax > 0:
        row = applicable[-1]
        threshold = d(row["above"])
        surcharge = tax * d(row["rate"])
        note = f"{pct(d(row['rate']))} because income is above {inr(threshold)}"
        if rule.get("surcharge_marginal_relief", True):
            base_tax, base_surcharge = _tax_before_cess(threshold, rule)
            ceiling = base_tax + base_surcharge + (taxable - threshold)
            if tax + surcharge > ceiling:
                surcharge = max(ZERO, ceiling - tax)
                note += ", limited by marginal relief"
        if trace:
            trace.surcharge = surcharge
            trace.surcharge_note = note
    return tax, surcharge


def annual_tax(taxable: Decimal, rule: dict, rounding_unit="1") -> TaxTrace:
    taxable = max(ZERO, taxable)
    trace = TaxTrace(taxable=taxable)
    tax, surcharge = _tax_before_cess(taxable, rule, trace)
    cess = (tax + surcharge) * d(rule.get("cess_rate", 0))
    trace.cess = cess
    trace.total = round_amount(tax + surcharge + cess, "nearest", rounding_unit)
    return trace
