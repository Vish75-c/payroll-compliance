"""Salary for a month: the rate in force each day, unpaid days, and what is earned after the unpaid-leave cut."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import policies as pol
from .explain import RuleRef
from .money import ZERO, d, inr, round_amount, to_str
from .periods import Period, Window, parse_date


@dataclass(frozen=True)
class _SalarySegment:
    first: date
    last: date
    effective_from: date
    rates: dict[str, Decimal]

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


@dataclass(frozen=True)
class _CutTerm:
    monthly_rate: Decimal
    days: Decimal
    description: str
    rounded_day_rate: Decimal | None = None


@dataclass
class Earnings:
    period: Period
    rate: dict[str, Decimal]
    rate_note: str
    days_in_month: int
    lop_days: Decimal
    not_employed_days: int
    unpaid_days: Decimal
    divisor: Decimal
    divisor_text: str
    cut: dict[str, Decimal]
    earned: dict[str, Decimal]
    rounding_mode: str
    lop_ref: RuleRef
    rounding_ref: RuleRef
    cycle_refs: list[RuleRef] = field(default_factory=list)  # the pay cycle setting, when it is not the calendar month
    attendance_text: str = ""  # e.g. " (counted 26 Oct to 25 Nov 2026)" when unpaid days follow an attendance cycle
    basis_days: int = 0  # days in a normal pay cycle; more or fewer than the days covered in a transition period
    period_rate: dict[str, Decimal] = field(default_factory=dict)  # pay for the days covered, before the unpaid cut
    total_cut: Decimal = ZERO
    regular_gross: Decimal = ZERO
    contracted_gross: Decimal = ZERO
    issues: list[dict] = field(default_factory=list)
    cut_by_segment: bool = False  # days not employed cut at the salary of their own part of the month
    cut_terms: dict[str, list[_CutTerm]] = field(default_factory=dict)
    capped_cuts: set[str] = field(default_factory=set)


def contracted_rate(compensation: list[dict], period: "Period | Window", employed_from: date | None = None) -> tuple[dict[str, Decimal], str, bool]:
    """Monthly salary by component for ``period`` (a calendar month, or the dates of a shifted pay cycle).

    If a revision starts mid-month, each part of the month is paid at its own rate,
    weighted by calendar days. When employment starts mid-month, earlier days use the starting salary.
    Returns (rates, note, backfilled). ``backfilled`` means a day
    the person was employed (on or after ``employed_from``) has no salary record; days before
    joining are not paid, so a salary that starts on the joining date is not backfilled.
    """
    employed_start = max(period.start, employed_from or period.start)
    segments, backfilled = _salary_segments(compensation, period, employed_start, period.end)
    units, note = _salary_units(segments)
    return {code: amount / period.days for code, amount in units.items()}, note, backfilled


def _salary_segments(compensation: list[dict], period: "Period | Window", employed_start: date,
                     employed_end: date) -> tuple[list[_SalarySegment], bool]:
    """Split the month at revisions in employment, extending the first and last employed rates to its edges."""
    if not compensation:
        return [], False
    # The input builder supplies already-known revisions in recording order. Standalone inputs can carry
    # recorded_on themselves. For equal or missing dates the last supplied record still wins.
    revised: dict[date, tuple[date, dict]] = {}
    for row in compensation:
        start = parse_date(row.get("effective_from")) or date.min
        recorded = parse_date(row.get("recorded_on")) or date.min
        if start not in revised or recorded >= revised[start][0]:
            revised[start] = (recorded, row["components"])
    revisions = sorted((start, values) for start, (_, values) in revised.items())

    backfilled = employed_start <= employed_end and revisions[0][0] > employed_start
    if employed_start > employed_end:
        employed_start, employed_end = period.start, period.end
    first = next((pair for pair in reversed(revisions) if pair[0] <= employed_start), revisions[0])
    active = [first] + [(start, amounts) for start, amounts in revisions if first[0] < start <= employed_end]

    segments = []
    for index, (start, amounts) in enumerate(active):
        next_start = active[index + 1][0] if index + 1 < len(active) else None
        first_day = period.start if index == 0 else start
        last_day = next_start - timedelta(days=1) if next_start else period.end
        segments.append(_SalarySegment(first_day, last_day, start, {code: d(amount) for code, amount in amounts.items()}))
    return segments, backfilled


def _salary_units(segments: list[_SalarySegment]) -> tuple[dict[str, Decimal], str]:
    """Sum salary amounts times the number of days before dividing to get the blended monthly rate."""
    if not segments:
        return {}, "no salary on record"
    units: dict[str, Decimal] = {}
    for segment in segments:
        for code, rate in segment.rates.items():
            units[code] = units.get(code, ZERO) + rate * segment.days
    if len(segments) == 1:
        start = segments[0].effective_from
        return units, f"salary in force from {start.isoformat()}" if start != date.min else "salary on record"
    parts = [f"{segment.days} days at {inr(sum(segment.rates.values(), ZERO))}/month" for segment in segments]
    return units, "salary changed during the month: " + ", ".join(parts)


def _divisor(lop_payload: dict, period: Period, window: Window, basis_days: int) -> tuple[Decimal, str]:
    divisor = lop_payload.get("divisor", "days_in_month")
    if divisor == "days_in_month":
        if (window.start, window.end) == (period.start, period.end):
            return Decimal(basis_days), f"{basis_days} days in {period.label.split()[0]}"
        if window.days != basis_days:
            return Decimal(basis_days), f"{basis_days} days in a normal pay cycle"
        return Decimal(basis_days), f"{basis_days} days in the pay cycle ({window.label})"
    value = d(divisor)
    return value, f"{to_str(value)} days ({lop_payload.get('source') or 'fixed by company setting'})"


@dataclass
class PayDates:
    """The dates one person's month covers: from their pay group (``pay_window`` in the input), or else the company's
    pay cycle setting (months stored before pay groups)."""
    salary: Window
    attendance: Window
    basis_days: int
    group: str = ""
    transition: bool = False
    refs: list[RuleRef] = field(default_factory=list)


def pay_dates_of(emp: dict, period: Period, versions: list[dict]) -> PayDates:
    pw = emp.get("pay_window")
    if pw:
        salary = Window(parse_date(pw["salary_from"]), parse_date(pw["salary_to"]))
        attendance = Window(parse_date(pw.get("attendance_from") or pw["salary_from"]), parse_date(pw.get("attendance_to") or pw["salary_to"]))
        return PayDates(salary, attendance, int(pw.get("basis_days") or salary.days), pw.get("group_name", ""), bool(pw.get("transition")))
    cycle = pol.resolve_company(versions, "pay_cycle", period)
    salary = pol.salary_window(versions, period)
    return PayDates(salary, pol.attendance_window(versions, period), salary.days,
                    refs=[cycle.ref] if cycle.get("mode", "calendar") != "calendar" else [])


def compute_earnings(emp: dict, period: Period, versions: list[dict], unpaid_days) -> Earnings:
    lop = pol.resolve_company(versions, "lop", period)
    rounding = pol.resolve_company(versions, "rounding", period)
    method = rounding.get("method", "nearest")
    mode = rounding.get("earnings", "per_component")

    # The dates this month pays for: the calendar month, or the person's pay group / company pay cycle.
    dates = pay_dates_of(emp, period, versions)
    window, attendance, basis = dates.salary, dates.attendance, dates.basis_days
    calendar_month = (period.start, period.end)

    days = window.days
    employed_start = max(window.start, parse_date(emp.get("date_of_joining")) or window.start)
    employed_end = min(window.end, parse_date(emp.get("date_of_exit")) or window.end)
    employed = max(0, (employed_end - employed_start).days + 1)
    not_employed = days - employed
    segments, backfilled = _salary_segments(emp.get("compensation") or [], window, employed_start, employed_end)
    salary_units, rate_note = _salary_units(segments)
    rate = {code: units / days for code, units in salary_units.items()}
    # A transition period (the first month on a new pay cycle) is paid for the days it covers: a 25-day period on a
    # 31-day cycle is 25/31 of the month. In every other month basis == days and the pay is the monthly rate.
    # salary_units / basis is therefore the pay for the days covered, before any unpaid cut.
    period_exact = {code: units / basis for code, units in salary_units.items()}
    if days != basis:
        rate_note = f"{rate_note}; transition to the new pay cycle: {window.label}, {days} of {basis} days"
    elif (window.start, window.end) != calendar_month:
        rate_note = f"{rate_note}; pay cycle {window.label}"
    issues = []
    if not rate:
        issues.append({"level": "error", "message": "No salary found for this employee. Pay is zero until salary data is provided."})
    if backfilled:
        issues.append({"level": "warning", "message": "Salary record starts after the month began; the earliest salary was used for the earlier days."})

    lop_days = d(unpaid_days)
    if lop_days < 0:
        issues.append({"level": "error", "message": f"Unpaid days cannot be negative ({to_str(lop_days)}); treated as 0."})
        lop_days = ZERO
    max_lop = Decimal(employed)
    if lop_days > max_lop:
        issues.append({"level": "warning", "message": f"{to_str(lop_days)} unpaid days is more than the {to_str(max_lop)} days employed this month; capped."})
        lop_days = max_lop
    unpaid = lop_days + not_employed

    divisor, divisor_text = _divisor(lop.payload, period, window, basis)
    day_rate_rule = lop.get("day_rate_rounding")

    def cut_term(units: Decimal, unit_days: int, count: Decimal, description: str) -> _CutTerm:
        day_rate = None
        if day_rate_rule:
            day_rate = round_amount(units / (unit_days * divisor), day_rate_rule["method"], day_rate_rule["unit"])
        return _CutTerm(units / unit_days, count, description, day_rate)

    # Days outside employment use the nearest employed salary. Unpaid leave has no dates here,
    # so its cut uses the average rate over days employed.
    by_segment = len(segments) > 1 and 0 < employed < days
    outside = []
    if by_segment:
        for segment in segments:
            employed_in_segment = (min(segment.last, employed_end) - max(segment.first, employed_start)).days + 1
            away = segment.days - max(0, employed_in_segment)
            if away:
                outside.append((segment, away))
    cut_exact: dict[str, Decimal] = {}
    cut_terms: dict[str, list[_CutTerm]] = {}
    capped_cuts: set[str] = set()
    cut_divisor = Decimal(1) if day_rate_rule else divisor * (employed if by_segment else days)
    uncapped_units = ZERO
    capped_salary_units = ZERO
    for comp, units in salary_units.items():
        if unpaid == 0:
            cut_exact[comp] = ZERO
            continue
        if unpaid == days:
            capped_salary_units += units
            cut_exact[comp] = period_exact[comp]
            continue
        if by_segment:
            terms = [cut_term(seg.rates.get(comp, ZERO), 1, Decimal(away), "days outside employment") for seg, away in outside]
            outside_units = sum((term.monthly_rate * term.days for term in terms), ZERO)
            employed_units = units - outside_units
            if lop_days:
                terms.append(cut_term(employed_units, employed, lop_days, "unpaid leave days at the average employed rate"))
            cut_units = outside_units * employed + employed_units * lop_days
        else:
            terms = [cut_term(units, days, unpaid, "unpaid days")]
            cut_units = units * unpaid
        if day_rate_rule:
            cut_units = sum(((term.rounded_day_rate or ZERO) * term.days for term in terms), ZERO)
        cut_terms[comp] = terms
        # The cut can never be more than the pay for the days covered (cut_units / cut_divisor > units / basis).
        if cut_units * basis > units * cut_divisor:
            capped_cuts.add(comp)
            capped_salary_units += units
            cut_exact[comp] = period_exact[comp]
        else:
            uncapped_units += cut_units
            cut_exact[comp] = cut_units / cut_divisor

    cut: dict[str, Decimal] = {}
    earned: dict[str, Decimal] = {}
    if mode == "per_component":
        period_rate = {comp: round_amount(v, method) for comp, v in period_exact.items()}
        for comp in rate:
            cut[comp] = min(round_amount(cut_exact[comp], method), period_rate[comp])
            earned[comp] = period_rate[comp] - cut[comp]
        total_cut = sum(cut.values(), ZERO)
        contracted = sum(period_rate.values(), ZERO)
        regular_gross = sum(earned.values(), ZERO)
    else:  # gross_only: components keep full precision, only the totals are rounded
        period_rate = dict(period_exact)
        for comp in rate:
            cut[comp] = cut_exact[comp]
            earned[comp] = period_exact[comp] - cut_exact[comp]
        contracted = round_amount(sum(salary_units.values(), ZERO) / basis, method)
        # Combine both kinds of cuts before division and rounding, just as for the salary blend.
        cut_total = (uncapped_units * basis + capped_salary_units * cut_divisor) / (cut_divisor * basis)
        total_cut = round_amount(cut_total, method)
        regular_gross = contracted - total_cut

    return Earnings(
        period=period,
        rate=rate,
        rate_note=rate_note,
        days_in_month=days,
        lop_days=lop_days,
        not_employed_days=not_employed,
        unpaid_days=unpaid,
        divisor=divisor,
        divisor_text=divisor_text,
        cut=cut,
        earned=earned,
        rounding_mode=mode,
        lop_ref=lop.ref,
        cycle_refs=dates.refs,
        attendance_text=f" (counted {attendance.label})" if (attendance.start, attendance.end) != calendar_month else "",
        basis_days=basis,
        period_rate=period_rate,
        rounding_ref=rounding.ref,
        total_cut=total_cut,
        regular_gross=regular_gross,
        contracted_gross=contracted,
        issues=issues,
        cut_by_segment=by_segment,
        cut_terms=cut_terms,
        capped_cuts=capped_cuts,
    )
