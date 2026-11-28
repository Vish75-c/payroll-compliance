"""The kinds of change a maker can make to a month, one class each.

A kind says, in one place:
* ``from_sentence`` — how a change the AI (or the rule-based parser) read from an instruction is checked;
* ``from_form`` — how a change typed into the "Add by hand" form is checked, and its one-line description;
* ``apply`` — what it does to the month's input for one employee;
* ``carry`` — what it still does in later months once its own month is final (most kinds: nothing).

``KINDS`` lists them by the ``type`` stored on each change. A new kind of change is a new class added to ``KINDS``;
the code that reads instructions, checks the form and builds a month's input does not change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from payroll_engine import policies as engine_policies
from payroll_engine.money import d, inr, to_str
from payroll_engine.periods import Period

from ...shared.permissions import RuleViolation
from ..hr_data.normalize import _split_gross, unpaid_days_for
from ..hr_data.values import MONTHS, ValueError_, parse_amount, parse_date, parse_number
from ..rules.policies import versions_for
from .cycle import month_text, window_for
from .models import ChangeRequest, PayrollRun

ITEM_LABELS = {
    "bonus": "Bonus", "incentive": "Incentive", "commission": "Commission", "reimbursement": "Reimbursement",
    "overtime": "Overtime", "other_earning": "Other earning", "advance_recovery": "Advance recovery",
    "loan_recovery": "Loan recovery", "other_deduction": "Other deduction",
}
EARNING_ITEMS = {"bonus", "incentive", "commission", "reimbursement", "overtime", "other_earning"}


_NUMERIC_DATE = re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b")


def date_written(text: str, when) -> bool:
    """Is ``when`` actually written in the instruction? The AI must not supply a date the person did not type.

    Accepted: the date in a numeric form that reads back to the same day ("2025-05-01", "01/05/2025"), or its
    month by name with the same day ("1 May", "May 1st") or with no day at all ("from May": the 1st). A year,
    when written next to the month, must match.
    """
    for written in _NUMERIC_DATE.findall(text):
        try:
            if parse_date(written) == when:
                return True
        except ValueError_:
            continue
    lowered = text.lower()
    month_words = [w for w, m in MONTHS.items() if m == when.month]
    for word in month_words:
        for match in re.finditer(rf"(?:\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?)?\b{word}\b\.?(?:\s+(\d{{1,2}})(?:st|nd|rd|th)?\b)?(?:,?\s+(\d{{4}}))?", lowered):
            day = match.group(1) or match.group(2)
            year = match.group(3)
            if year and int(year) != when.year:
                continue
            if (int(day) if day else 1) == when.day:
                return True
    return False


class Problem(Exception):
    """Something wrong with a change read from an instruction: shown to the maker, and the change cannot be confirmed."""


@dataclass
class Sentence:
    """A change read from an instruction, and what is needed to check it against what was typed."""
    run: PayrollRun
    period: Period
    emp: dict
    text: str
    change: dict
    written_amounts: set[str]
    numbers_in_text: set[str]

    def amount(self) -> Decimal:
        """An amount must be typed in the instruction: the AI may copy it, never work it out."""
        written = self.change.get("amount_as_written")
        if not written:
            raise Problem(f"No amount is written in the instruction for {self.emp['name']}. Amounts must be typed, never worked out by AI.")
        try:
            amount = parse_amount(written)
        except ValueError_:
            raise Problem(f"Could not read the amount '{written}'.") from None
        if to_str(amount) not in self.written_amounts:
            raise Problem(f"The amount {inr(amount)} does not appear in the instruction, so it was not used. Amounts must be typed, never worked out by AI.")
        if amount <= 0:
            raise Problem("The amount must be more than zero.")
        return amount


@dataclass
class Form:
    """A change typed into the form by a maker."""
    run: PayrollRun
    period: Period
    emp: dict
    data: dict
    reason: str

    @property
    def who(self) -> str:
        return f"{self.emp['name']} ({self.emp['code']})"

    def days(self) -> Decimal:
        try:
            days = parse_number(str(self.data.get("days", "")).strip())
        except ValueError_:
            raise RuleViolation("Enter the number of unpaid days, for example 2 or 0.5.") from None
        if days <= 0 or (days * 2) % 1:
            raise RuleViolation("Unpaid days must be more than zero, in whole or half days.")
        return days

    def dates_within(self, period: Period, item: dict, days: Decimal, outside: str) -> str:
        """Optional from/to dates of an absence: checked, stored on the item, and described (" (11 Aug to 12 Aug 2025)")."""
        start, end = self.data.get("from") or "", self.data.get("to") or self.data.get("from") or ""
        if not start:
            return ""
        try:
            first, last = parse_date(start), parse_date(end)
        except ValueError_:
            raise RuleViolation("Could not read the dates.") from None
        window = window_for(self.run.company, period, self.emp["code"], self.run.snapshot.data).attendance
        if not (window.start <= first <= last <= window.end):
            raise RuleViolation(outside)
        covered = (last - first).days + 1
        if days > covered:
            raise RuleViolation(f"{to_str(days)} days do not fit between {first:%d %b} and {last:%d %b}.")
        if days < covered - Decimal("0.5"):
            # Only the last half day may be missing (a half day at one end); fewer days means the dates are wrong.
            raise RuleViolation(f"{first:%d %b} to {last:%d %b} is {covered} day{'s' if covered != 1 else ''}, "
                                f"so the unpaid days must be {covered} or {to_str(covered - Decimal('0.5'))}, not {to_str(days)}.")
        item.update({"from": first.isoformat(), "to": last.isoformat()})
        return f" ({first:%d %b}" + (f" to {last:%d %b %Y})" if last != first else f" {first:%Y})")


@dataclass
class EmployeeMonth:
    """One employee's part of a month's input that changes can alter."""
    compensation: list[dict]
    unpaid: Decimal
    extra_unpaid: Decimal = Decimal(0)
    late_unpaid: dict[str, Decimal] = field(default_factory=dict)  # earlier month -> unpaid days reported after it was paid
    one_time: list[dict] = field(default_factory=list)

    def set_salary(self, effective_from: str, components: dict):
        self.compensation = [r for r in self.compensation if r.get("effective_from") != effective_from]
        self.compensation.append({"effective_from": effective_from, "components": components})

    def add_late_unpaid(self, month: str, days: str):
        self.late_unpaid[month] = self.late_unpaid.get(month, d(0)) + d(days)


# --------------------------------------------------------------------------- helpers shared by the kinds

def final_months(run: PayrollRun) -> list[PayrollRun]:
    return list(PayrollRun.objects.filter(company=run.company, run_type=PayrollRun.TYPE_REGULAR, status=PayrollRun.FINALIZED,
                                          period__lt=run.period).order_by("period"))


def unpaid_so_far(run: PayrollRun, code: str):
    """Unpaid days the month already has for one person: from the HR data (or a confirmed figure that replaces
    it), plus unpaid absences already added by hand."""
    period = run.period_obj
    window = window_for(run.company, period, code, run.snapshot.data).attendance
    base, extra = unpaid_days_for(run.snapshot.data, code, period, window), d(0)
    for ch in ChangeRequest.objects.filter(company=run.company, period=run.period, status=ChangeRequest.CONFIRMED):
        for item in ch.proposal.get("items", []):
            if item.get("employee_code") != code:
                continue
            if item["type"] == "unpaid_days":
                base = d(item["unpaid_days"])
            elif item["type"] == "extra_unpaid_days":
                extra += d(item["unpaid_days"])
    return base, extra


def late_days(run: PayrollRun, code: str, month: str):
    """Unpaid days already reported late for one person and one paid month (in this month or months since paid)."""
    periods = [r.period for r in final_months(run)] + [run.period]
    total = d(0)
    for ch in ChangeRequest.objects.filter(company=run.company, status=ChangeRequest.CONFIRMED, period__in=periods):
        for item in ch.proposal.get("items", []):
            if item.get("type") == "past_unpaid_days" and item.get("employee_code") == code and item.get("month") == month:
                total += d(item["unpaid_days"])
    return total


def joined_with_and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def split_salary(run: PayrollRun, amount: Decimal, start) -> dict:
    structure = engine_policies.resolve_company(versions_for(run.company), "salary_structure", Period.of(start))
    return {k: to_str(v) for k, v in _split_gross(amount, structure.payload).items()}


# --------------------------------------------------------------------------- the kinds

class Kind:
    type = ""

    def from_sentence(self, s: Sentence, item: dict):
        raise Problem(f"Unknown kind of change '{self.type}'.")

    def from_form(self, f: Form, item: dict) -> str:
        raise RuleViolation("Pick what to add: unpaid absence, a salary change, a payment or a deduction.")

    def apply(self, item: dict, month: EmployeeMonth, change: ChangeRequest):
        """What a confirmed change of this month does to the employee's input."""

    def carry(self, item: dict, month: EmployeeMonth):
        """What a change confirmed in a month that is now final still does in a later month. Most do nothing."""


class OneTime(Kind):
    """A one-time payment (bonus, incentive, reimbursement…) or deduction (advance or loan recovery…)."""

    def __init__(self, type_: str, earning: bool):
        self.type, self.earning = type_, earning

    def _code(self, wanted: str | None) -> str:
        return wanted or ("other_earning" if self.earning else "other_deduction")

    def _summary(self, label: str, amount, who: str, period: Period) -> str:
        verb = "Pay" if self.earning else "Deduct"
        return f"{verb} a one-time {label.lower()} of {inr(amount)} {'to' if self.earning else 'from'} {who} in {period.label}."

    def from_sentence(self, s, item):
        amount = s.amount()
        item["amount"] = to_str(amount)
        code = self._code(s.change.get("item"))
        if (code in EARNING_ITEMS) != self.earning:
            code = "other_earning" if self.earning else "other_deduction"
        item["item"] = code
        item["label"] = ITEM_LABELS.get(code, code)
        taxable = s.change.get("taxable")
        item["taxable"] = (code != "reimbursement") if taxable is None else bool(taxable)
        item["summary"] = (
            self._summary(item["label"], amount, f"{s.emp['name']} ({s.emp['code']})", s.period)
            + (" It is taxable and is not part of PF wages." if self.earning and item["taxable"] else "")
            + (" It is not taxable." if self.earning and not item["taxable"] else "")
        )

    def from_form(self, f, item):
        code = self._code(f.data.get("item"))
        if (code in EARNING_ITEMS) != self.earning or code not in ITEM_LABELS:
            raise RuleViolation("Pick what kind of payment or deduction it is.")
        try:
            amount = parse_amount(str(f.data.get("amount", "")).strip())
        except ValueError_:
            raise RuleViolation("Enter the amount in rupees, for example 10000.") from None
        if amount <= 0:
            raise RuleViolation("The amount must be more than zero.")
        taxable = (code != "reimbursement") if f.data.get("taxable") is None else bool(f.data.get("taxable"))
        label = ITEM_LABELS[code]
        item.update({"item": code, "label": label, "amount": to_str(amount), "taxable": taxable, "reason": f.reason})
        item["summary"] = (self._summary(label, amount, f.who, f.period)
                           + ((" It is taxable." if taxable else " It is not taxable.") if self.earning else "")
                           + (f" Reason: {f.reason}." if f.reason else ""))
        return f"{label}: {f.emp['name']}, {inr(amount)}" + (f". {f.reason}" if f.reason else "")

    def apply(self, item, month, change):
        month.one_time.append({
            "ref": f"change:{change.pk}",
            "kind": "earning" if self.earning else "deduction",
            "code": item["item"],
            "label": item["label"],
            "amount": item["amount"],
            "taxable": item.get("taxable", True),
            "source_text": change.text,
            **({"exemption": item["exemption"]} if item.get("exemption") else {}),
        })


class UnpaidDays(Kind):
    """Replace the month's unpaid days from the HR data with a figure from an instruction."""
    type = "unpaid_days"

    def from_sentence(self, s, item):
        written = s.change.get("unpaid_days_as_written")
        try:
            days = parse_number(written)
        except (ValueError_, TypeError):
            raise Problem("The number of unpaid days is not written in the instruction.") from None
        if to_str(days) not in s.numbers_in_text:
            raise Problem(f"{to_str(days)} days does not appear in the instruction, so it was not used.")
        if days < 0 or days > window_for(s.run.company, s.period, s.emp["code"], s.run.snapshot.data).attendance.days:
            raise Problem(f"{to_str(days)} unpaid days is not possible in {s.period.label}.")
        item["unpaid_days"] = to_str(days)
        item["summary"] = f"Set {s.emp['name']}'s ({s.emp['code']}) unpaid days in {s.period.label} to {to_str(days)} (replaces the figure from the HR data)."

    def apply(self, item, month, change):
        month.unpaid = d(item["unpaid_days"])


class ExtraUnpaidDays(Kind):
    """An unpaid absence this month that the HR data does not show (e.g. absent without approval)."""
    type = "extra_unpaid_days"

    def from_form(self, f, item):
        period = f.period
        if not f.reason:
            raise RuleViolation("Say why, for example \"Absent without approval on 14-15 July\". It is kept in the audit trail.")
        window = window_for(f.run.company, period, f.emp["code"], f.run.snapshot.data).attendance
        if f.data.get("dates"):
            days, dates = self._listed_dates(f, item)
        else:
            days = f.days()
            dates = f.dates_within(period, item, days, f"The dates must be within {month_text(window, period)}, the month being prepared.")
        base, extra = unpaid_so_far(f.run, f.emp["code"])
        if base + extra + days > window.days:
            raise RuleViolation(f"{f.who} would have {to_str(base + extra + days)} unpaid days in {period.label}, more than the "
                                f"{window.days} days in the month (already {to_str(base + extra)}).")
        item.update({"unpaid_days": to_str(days), "reason": f.reason})
        item["summary"] = (f"Add {to_str(days)} unpaid day{'s' if days != 1 else ''} for {f.who} in {period.label}{dates}: {f.reason}. "
                           f"On top of the {to_str(base + extra)} unpaid day{'s' if base + extra != 1 else ''} already counted.")
        return f"Unpaid absence: {f.emp['name']}, {to_str(days)} day{'s' if days != 1 else ''}{dates}. {f.reason}"

    @staticmethod
    def _listed_dates(f: Form, item: dict) -> tuple[Decimal, str]:
        """Whole days given as a list (e.g. the days with no check-in), not necessarily one after the other."""
        period = f.period
        window = window_for(f.run.company, period, f.emp["code"], f.run.snapshot.data).attendance
        try:
            listed = sorted({parse_date(str(x)) for x in f.data["dates"]})
        except ValueError_:
            raise RuleViolation("Could not read the dates.") from None
        if not all(window.start <= x <= window.end for x in listed):
            raise RuleViolation(f"The dates must be within {month_text(window, period)}, the month being prepared.")
        item["dates"] = [x.isoformat() for x in listed]
        text = ", ".join(f"{x:%d}" for x in listed[:-1]) + (" and " if len(listed) > 1 else "") + f"{listed[-1]:%d %b %Y}"
        return Decimal(len(listed)), f" ({text})"

    def apply(self, item, month, change):
        month.extra_unpaid += d(item["unpaid_days"])


class PastUnpaidDays(Kind):
    """Unpaid days found (or counted by mistake) in a month already paid: that month is worked out again and the
    difference is recovered (or paid back) now. It keeps counting in later months, so it is never undone."""
    type = "past_unpaid_days"

    def from_form(self, f, item):
        run, period, reason, who = f.run, f.period, f.reason, f.who
        if not reason:
            raise RuleViolation("Say why, for example \"Absent without approval, found in the July attendance review\". It is kept in the audit trail.")
        month = str(f.data.get("month") or "")
        past = next((r for r in final_months(run) if r.period == month), None)
        if past is None:
            raise RuleViolation("Pick an earlier month that is already paid. For this month, use \"Unpaid absence\".")
        arrears = engine_policies.resolve_company(versions_for(run.company), "arrears_policy", period)
        if not arrears.get("enabled", True):
            raise RuleViolation("Arrears are switched off on Rules & settings, so earlier months are not worked out again.")
        if past.period_obj.months_until(period) > int(arrears.get("lookback_months", 12)):
            raise RuleViolation(f"{past.period_obj.label} is further back than the arrears setting looks ({arrears.get('lookback_months', 12)} months).")
        paid = {e["code"]: e for e in (past.input or {}).get("employees", [])}.get(f.emp["code"])
        if not paid:
            raise RuleViolation(f"{who} was not paid in {past.period_obj.label}.")
        days = f.days()
        remove = f.data.get("direction") == "remove"  # unpaid days counted by mistake in that month: paid back now
        pp = past.period_obj
        past_window = window_for(run.company, pp, f.emp["code"], run.snapshot.data).attendance
        dates = f.dates_within(pp, item, days, f"The dates must be within {month_text(past_window, pp)}.")
        already = d(paid.get("unpaid_days", "0")) + late_days(run, f.emp["code"], month)
        label = pp.label
        plural = "s" if days != 1 else ""
        if remove:
            if days > already:
                raise RuleViolation(f"{who} had {to_str(already)} unpaid day{'s' if already != 1 else ''} in {label}; "
                                    f"{to_str(days)} cannot be taken off.")
            item.update({"month": month, "unpaid_days": to_str(-days), "reason": reason, "direction": "remove"})
            item["summary"] = (f"Take {to_str(days)} unpaid day{plural} off {who}'s {label}{dates}: {reason}. {label} is already paid, so "
                               f"the pay cut for these days is paid back in {period.label}, worked out with {label}'s rules.")
            return f"Unpaid days counted by mistake in {label}: {f.emp['name']}, {to_str(days)} day{plural}{dates}. {reason}"
        if already + days > past_window.days:
            raise RuleViolation(f"{who} would have {to_str(already + days)} unpaid days in {label}, more than the "
                                f"{past_window.days} days in that month (already {to_str(already)}).")
        item.update({"month": month, "unpaid_days": to_str(days), "reason": reason})
        item["summary"] = (f"Add {to_str(days)} unpaid day{plural} for {who} in {label}{dates}: {reason}. {label} is already "
                           f"paid, so the pay for these days is recovered in {period.label}, worked out with {label}'s rules.")
        return f"Unpaid absence in {label}: {f.emp['name']}, {to_str(days)} day{plural}{dates}. {reason}"

    def apply(self, item, month, change):
        month.add_late_unpaid(item["month"], item["unpaid_days"])

    def carry(self, item, month):
        month.add_late_unpaid(item["month"], item["unpaid_days"])


class SalaryRevision(Kind):
    """A new monthly salary from a date, split by the salary structure. Months already paid get arrears; it keeps
    counting in later months, so the raise is never taken back."""
    type = "salary_revision"

    def from_sentence(self, s, item):
        amount = s.amount()
        item["amount"] = to_str(amount)
        start = s.change.get("effective_from")
        try:
            start_date = parse_date(start) if start else None
        except ValueError_:
            start_date = None
        if not start_date:
            raise Problem(f"The instruction does not say from which date {s.emp['name']}'s new salary starts.")
        if not date_written(s.text, start_date):
            raise Problem(f"The start date ({start_date:%d %b %Y}) is not written in the instruction, so it was not used. "
                          "Write it, for example 'from 1 May 2025'.")
        components = split_salary(s.run, amount, start_date)
        item.update({"effective_from": start_date.isoformat(), "components": components})
        parts = ", ".join(f"{k.replace('_', ' ')} {inr(v)}" for k, v in components.items())
        item["summary"] = (f"Change {s.emp['name']}'s ({s.emp['code']}) monthly salary to {inr(amount)} from {start_date.isoformat()} "
                           f"(split by the company salary structure: {parts}). Past months already paid get arrears.")

    def from_form(self, f, item):
        run, period, reason, who = f.run, f.period, f.reason, f.who
        try:
            amount = parse_amount(str(f.data.get("amount", "")).strip())
            start = parse_date(str(f.data.get("effective_from", "")).strip())
        except ValueError_:
            raise RuleViolation("Enter the new monthly salary in rupees and the date it starts.") from None
        if not start or amount <= 0:
            raise RuleViolation("Enter the new monthly salary in rupees and the date it starts.")
        window = window_for(run.company, period, f.emp["code"], run.snapshot.data).salary
        if start > window.end:
            raise RuleViolation(f"A salary that starts after {month_text(window, period)} is entered in the month it starts (or in the HR tool).")
        staff = next((e for e in run.snapshot.data["employees"] if e["code"] == f.emp["code"]), {})
        joined = parse_date(staff.get("date_of_joining")) if staff.get("date_of_joining") else None
        if joined and start < joined:
            raise RuleViolation(f"{who} joined on {joined:%d %b %Y}; the new salary cannot start before that.")
        components = split_salary(run, amount, start)
        paid_before = [r.period_obj.label for r in final_months(run)
                       if window_for(run.company, r.period_obj, f.emp["code"], run.snapshot.data).salary.end >= start]
        parts = ", ".join(f"{k.replace('_', ' ')} {inr(v)}" for k, v in components.items())
        item.update({"amount": to_str(amount), "effective_from": start.isoformat(), "components": components, "reason": reason})
        item["summary"] = (f"Change {who}'s monthly salary to {inr(amount)} from {start:%d %b %Y} (split by the company salary structure: {parts})."
                           + (f" {joined_with_and(paid_before)} {'is' if len(paid_before) == 1 else 'are'} already paid: the difference is paid as arrears in {period.label}."
                              if paid_before else "")
                           + (f" Reason: {reason}." if reason else ""))
        return f"Salary change: {f.emp['name']}, {inr(amount)} a month from {start:%d %b %Y}" + (f". {reason}" if reason else "")

    def apply(self, item, month, change):
        month.set_salary(item["effective_from"], item["components"])

    def carry(self, item, month):
        month.set_salary(item["effective_from"], item["components"])


class VoluntaryPF(Kind):
    """More PF the employee chose to pay this month (VPF): taken from net pay into the same PF account."""
    type = "vpf"

    def apply(self, item, month, change):
        month.one_time.append({"ref": f"change:{change.pk}", "kind": "vpf", "code": "vpf", "label": item.get("label") or "Voluntary PF",
                               "amount": item["amount"], "source_text": change.text})


class InKind(Kind):
    """A perquisite (taxed, not paid in cash) or a TDS amount entered for the month instead of the one worked out."""

    def __init__(self, type_: str):
        self.type = type_

    def apply(self, item, month, change):
        month.one_time.append({"ref": f"change:{change.pk}", "kind": self.type, "code": item.get("item") or self.type,
                               "label": item.get("label") or self.type.replace("_", " ").capitalize(), "amount": item["amount"],
                               "source_text": change.text})


KINDS: dict[str, Kind] = {k.type: k for k in (
    InKind("perquisite"),
    InKind("tds_override"),
    OneTime("one_time_earning", earning=True),
    OneTime("one_time_deduction", earning=False),
    VoluntaryPF(),
    UnpaidDays(),
    ExtraUnpaidDays(),
    PastUnpaidDays(),
    SalaryRevision(),
)}


def kind_of(type_: str | None) -> Kind:
    found = KINDS.get(type_ or "")
    if found:
        return found
    unknown = Kind()
    unknown.type = str(type_)
    return unknown
