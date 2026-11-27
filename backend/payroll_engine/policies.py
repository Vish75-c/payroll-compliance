"""Rules and company settings are data: versioned rows with a start date.

A policy version looks like::

    {"id": "7", "key": "pf", "scope": "statutory", "jurisdiction": "IN",
     "effective_from": "2025-04-01", "payload": {...}, "title": "..."}

Versions are never edited. A change is a new version with a later ``effective_from``.
``resolve`` picks, for a given date, the latest version that has started. This is a pure
function, so the rule that applied to any past month can always be found again.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .explain import RuleRef
from .money import ROUNDING_METHODS, d
from .periods import Period, Window, cycle_window, parse_date

STATUTORY = "statutory"
COMPANY = "company"
OVERRIDE = "override"  # what-if values used only by a draft re-run
MONTH = "month"  # a value set for one payroll month when it was opened (e.g. its day count)
DEFAULT = "default"

# Rules are picked as of the first day of the pay month. A rule that starts on 15 July
# therefore first applies to August. The UI warns when a rule starts mid-month.
RULE_AS_OF = "period_start"

STATUTORY_KEYS = {
    "pf": "Provident Fund",
    "esi": "Employees' State Insurance",
    "professional_tax": "Professional tax",
    "income_tax": "Income tax (TDS)",
    "wage_definition": "Labour Codes wage definition (50% rule)",
}

COMPANY_DEFAULTS: dict[str, dict] = {
    "lop": {"divisor": "days_in_month", "day_rate_rounding": None},
    "rounding": {"method": "nearest", "earnings": "per_component"},
    "pf_policy": {"wage_base": "capped", "employer_wage_base": "same", "arrears_contribution": "recompute_delta"},
    "esi_policy": {"eligibility_wage": "gross_this_month", "contribution_wage": "gross_this_month"},
    "pt_policy": {"basis": "gross_this_month"},
    "tds_policy": {
        "method": "projected_annual",
        "one_time_income": "spread",
        "annual_tax_rounding_unit": 1,
        "monthly_rounding": "nearest",
    },
    "arrears_policy": {"enabled": True, "lookback_months": 12},
    "salary_structure": {
        "components": [
            {"code": "basic", "share": "0.50"},
            {"code": "hra", "share": "0.20"},
            {"code": "special_allowance", "share": "remainder"},
        ]
    },
    # Whether the Labour Codes 50% wage rule is applied once it is in force. Following the government is the default;
    # "not_applied" is for an establishment advised that the Codes do not cover it yet.
    "labour_codes": {"apply": "as_government"},
    # Used only to suggest possible unpaid absences from attendance; never in a calculation.
    "work_calendar": {"weekly_off": ["saturday", "sunday"], "holidays": []},
    # Which dates a pay month covers. "calendar": 1st to last day. "attendance_cutoff": salary for the calendar
    # month, unpaid days counted from ``start_day`` of the month before. "shifted": salary and unpaid days both
    # counted from ``start_day`` of the month before (with 26, November is 26 Oct to 25 Nov).
    "pay_cycle": {"mode": "calendar", "start_day": 1},
    # The day of the month being paid after which people still waiting for HR data are put on hold, so everyone
    # else is paid on time. A day past the month's end means its last day. Never used in a calculation.
    "cut_off": {"day": 28},
}

COMPANY_KEYS = {
    "lop": "Unpaid leave: what one day is worth",
    "rounding": "Rounding of earnings",
    "pf_policy": "PF base and arrears",
    "esi_policy": "ESI wage used for eligibility and contribution",
    "pt_policy": "Pay used for professional tax",
    "tds_policy": "TDS method",
    "arrears_policy": "Arrears",
    "salary_structure": "Salary split for new salaries",
    "work_calendar": "Working days and holidays (for attendance)",
    "labour_codes": "Government rules: Labour Codes",
    "pay_cycle": "Pay cycle",
    "cut_off": "Cut-off for missing HR data",
}

PAY_CYCLE_MODES = ["calendar", "attendance_cutoff", "shifted"]


@dataclass
class Resolved:
    key: str
    payload: dict
    ref: RuleRef

    def get(self, name, default=None):
        return self.payload.get(name, default)


class PolicyMissing(Exception):
    pass


def _as_of(period: Period) -> date:
    return period.start if RULE_AS_OF == "period_start" else period.end


_SCOPE_PRIORITY = {DEFAULT: 0, STATUTORY: 1, COMPANY: 1, MONTH: 2, OVERRIDE: 3}


def resolve(versions: list[dict], key: str, period: Period, jurisdiction: str | None = None) -> Resolved | None:
    """Pick the version of ``key`` in force for ``period``.

    Latest ``effective_from`` on or before the as-of date wins; a what-if override beats a
    stored version; between two versions with the same start date the later-recorded wins.
    """
    as_of = _as_of(period)
    candidates = []
    for v in versions:
        if v["key"] != key:
            continue
        if jurisdiction is not None and v.get("jurisdiction", "") != jurisdiction:
            continue
        start = parse_date(v["effective_from"])
        if start is None or start > as_of:
            continue
        candidates.append(v)
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda v: (
            parse_date(v["effective_from"]),
            _SCOPE_PRIORITY.get(v.get("scope"), 1),
            str(v.get("recorded_at", "")),
            _id_sort_key(v.get("id")),
        ),
    )
    ref = RuleRef(
        key=key,
        version_id=str(best.get("id", "")),
        effective_from=str(best["effective_from"])[:10],
        scope=best.get("scope", ""),
        jurisdiction=best.get("jurisdiction", ""),
        title=best.get("title", "") or STATUTORY_KEYS.get(key) or COMPANY_KEYS.get(key, key),
    )
    return Resolved(key=key, payload=best["payload"], ref=ref)


def _id_sort_key(value):
    text = str(value or "")
    return (0, int(text)) if text.isdigit() else (1, text)


def resolve_statutory(versions, key, period, jurisdiction) -> Resolved:
    found = resolve(versions, key, period, jurisdiction)
    if found is None:
        raise PolicyMissing(f"No '{key}' rule for {jurisdiction} is in force on {_as_of(period)}")
    return found


def resolve_company(versions, key, period) -> Resolved:
    """Company settings fall back to a documented built-in default when none is stored."""
    found = resolve([v for v in versions if v.get("scope") in (COMPANY, MONTH, OVERRIDE)], key, period)
    default = COMPANY_DEFAULTS[key]
    if found is None:
        return Resolved(
            key=key,
            payload=copy.deepcopy(default),
            ref=RuleRef(key=key, version_id="default", effective_from="", scope=DEFAULT, title=COMPANY_KEYS[key]),
        )
    merged = copy.deepcopy(default)
    merged.update(found.payload)
    return Resolved(key=key, payload=merged, ref=found.ref)


def _cycle_window(versions, period: Period, modes: tuple[str, ...]) -> Window:
    cycle = resolve_company(versions, "pay_cycle", period)
    day = int(cycle.get("start_day") or 1) if cycle.get("mode") in modes else 1
    return cycle_window(period, day)


def salary_window(versions, period: Period) -> Window:
    """The dates whose salary a pay month pays: the calendar month unless the whole cycle is shifted."""
    return _cycle_window(versions, period, ("shifted",))


def attendance_window(versions, period: Period) -> Window:
    """The dates whose unpaid days a pay month counts: the calendar month only under the calendar cycle."""
    return _cycle_window(versions, period, ("attendance_cutoff", "shifted"))


# --------------------------------------------------------------------------- validation

def validate_payload(key: str, payload: dict) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]
    validator = _VALIDATORS.get(key)
    if validator is None:
        return [f"unknown rule/setting key '{key}'"]
    errors: list[str] = []
    if key in COMPANY_DEFAULTS:
        # A misspelt field would be silently replaced by its default; refuse it instead.
        known = set(COMPANY_DEFAULTS[key])
        for name in sorted(set(payload) - known):
            errors.append(f"unknown field '{name}' for '{key}' (known: {', '.join(sorted(known))})")
    try:
        validator(payload, errors)
    except (ValueError, TypeError, KeyError) as exc:  # malformed numbers etc.
        errors.append(str(exc))
    return errors


def _num(payload, name, errors, required=True, minimum=None, maximum=None):
    if name not in payload or payload[name] in (None, ""):
        if required:
            errors.append(f"'{name}' is required")
        return None
    try:
        value = d(payload[name])
    except ValueError:
        errors.append(f"'{name}' must be a number")
        return None
    if minimum is not None and value < d(minimum):
        errors.append(f"'{name}' must be at least {minimum}")
    if maximum is not None and value > d(maximum):
        errors.append(f"'{name}' must be at most {maximum}")
    return value


def _choice(payload, name, options, errors, required=True):
    value = payload.get(name)
    if value is None:
        if required:
            errors.append(f"'{name}' is required (one of {', '.join(map(str, options))})")
        return
    if value not in options:
        errors.append(f"'{name}' must be one of {', '.join(map(str, options))}, got {value!r}")


def _v_pf(p, e):
    _num(p, "employee_rate", e, minimum=0, maximum=1)
    _num(p, "employer_rate", e, minimum=0, maximum=1)
    _num(p, "wage_ceiling", e, minimum=0)
    _num(p, "eps_rate", e, required=False, minimum=0, maximum=1)
    _num(p, "eps_wage_ceiling", e, required=False, minimum=0)
    if not isinstance(p.get("wage_components"), list) or not p.get("wage_components"):
        e.append("'wage_components' must be a non-empty list, e.g. [\"basic\", \"da\"]")
    _choice(p, "rounding", list(ROUNDING_METHODS), e, required=False)


def _v_esi(p, e):
    _num(p, "threshold", e, minimum=0)
    _num(p, "employee_rate", e, minimum=0, maximum=1)
    _num(p, "employer_rate", e, minimum=0, maximum=1)
    _num(p, "employee_daily_wage_exemption", e, required=False, minimum=0)
    _choice(p, "rounding", list(ROUNDING_METHODS), e, required=False)


def _v_pt(p, e):
    if "levied" in p and not isinstance(p["levied"], bool):
        e.append("'levied' must be true or false")
        return
    if p.get("levied") is False:
        # A state that does not charge professional tax: stored as a rule so a payslip can say so, not "no rule".
        if p.get("slabs"):
            e.append("a state that does not charge professional tax ('levied': false) has no slabs")
        return
    slabs = p.get("slabs")
    if not isinstance(slabs, list) or not slabs:
        e.append("'slabs' must be a non-empty list like [{\"min\": 0, \"amount\": 0}, {\"min\": 25000, \"amount\": 200}]")
        return
    for i, slab in enumerate(slabs):
        where = f"slabs[{i}]"
        if not isinstance(slab, dict):
            e.append(f"{where} must be an object")
            continue
        if ("min" in slab) == ("above" in slab):
            e.append(f"{where} needs exactly one of 'min' (>=) or 'above' (>)")
        for bound in ("min", "above", "max"):
            if bound in slab and slab[bound] is not None:
                try:
                    d(slab[bound])
                except ValueError:
                    e.append(f"{where}.{bound} must be a number")
        if ("amount" in slab) == ("rate" in slab):
            e.append(f"{where} needs exactly one of 'amount' or 'rate'")
        for numeric in ("amount", "rate"):
            if numeric in slab:
                try:
                    d(slab[numeric])
                except ValueError:
                    e.append(f"{where}.{numeric} must be a number")
        if "gender" in slab and slab["gender"] not in ("M", "F", "O"):
            e.append(f"{where}.gender must be M, F or O")
        if "months" in slab and (
            not isinstance(slab["months"], list) or not all(isinstance(m, int) and 1 <= m <= 12 for m in slab["months"])
        ):
            e.append(f"{where}.months must be a list of month numbers 1-12")
    _num(p, "due_day_next_month", e, required=False, minimum=1, maximum=31)


def _v_income_tax(p, e):
    _choice(p, "regime", ["new"], e)
    _num(p, "standard_deduction", e, minimum=0)
    _num(p, "cess_rate", e, minimum=0, maximum=1)
    slabs = p.get("slabs")
    if not isinstance(slabs, list) or not slabs:
        e.append("'slabs' must be a non-empty list like [{\"upto\": 400000, \"rate\": 0}, ..., {\"upto\": null, \"rate\": 0.3}]")
    else:
        previous = Decimal("-1")
        for i, slab in enumerate(slabs):
            upto = slab.get("upto")
            if upto is None and i != len(slabs) - 1:
                e.append("only the last slab may have 'upto': null")
            if upto is not None:
                if d(upto) <= previous:
                    e.append("slab 'upto' values must increase")
                previous = d(upto)
            _num(slab, "rate", e, minimum=0, maximum=1)
        if slabs[-1].get("upto") is not None:
            e.append("the last slab must have 'upto': null (no upper limit)")
    rebate = p.get("rebate")
    if rebate is not None:
        _num(rebate, "income_upto", e, minimum=0)
        _num(rebate, "max_rebate", e, minimum=0)
    for i, row in enumerate(p.get("surcharge") or []):
        _num(row, "above", e, minimum=0)
        _num(row, "rate", e, minimum=0, maximum=1)


def _v_wage_definition(p, e):
    if not isinstance(p.get("enabled"), bool):
        e.append("'enabled' must be true or false")
    _num(p, "max_exclusion_share", e, minimum=0, maximum=1)
    if not isinstance(p.get("wage_components"), list) or not p.get("wage_components"):
        e.append("'wage_components' must be a non-empty list")


def _v_lop(p, e):
    divisor = p.get("divisor")
    if divisor != "days_in_month":
        try:
            if d(divisor) <= 0:
                raise ValueError
        except (ValueError, TypeError):
            e.append("'divisor' must be \"days_in_month\" or a positive number such as 30 or 26")
    rounding = p.get("day_rate_rounding")
    if rounding is not None:
        if not isinstance(rounding, dict):
            e.append("'day_rate_rounding' must be null or {\"unit\": 0.01, \"method\": \"nearest\"}")
        else:
            _num(rounding, "unit", e, minimum="0.0001")
            _choice(rounding, "method", list(ROUNDING_METHODS), e)


def _v_rounding(p, e):
    _choice(p, "method", list(ROUNDING_METHODS), e)
    _choice(p, "earnings", ["per_component", "gross_only"], e)


def _v_pf_policy(p, e):
    _choice(p, "wage_base", ["capped", "full"], e)
    _choice(p, "employer_wage_base", ["same", "capped", "full"], e, required=False)
    _choice(p, "arrears_contribution", ["recompute_delta", "none"], e, required=False)


_WAGE_CHOICES = ["gross_this_month", "regular_earned", "contracted_monthly", "gross_excluding_one_time"]


def _v_esi_policy(p, e):
    _choice(p, "eligibility_wage", _WAGE_CHOICES, e)
    _choice(p, "contribution_wage", _WAGE_CHOICES, e)


def _v_pt_policy(p, e):
    _choice(p, "basis", _WAGE_CHOICES, e)


def _v_tds_policy(p, e):
    _choice(p, "method", ["projected_annual"], e)
    _choice(p, "one_time_income", ["spread", "tax_in_month"], e)
    _num(p, "annual_tax_rounding_unit", e, required=False, minimum=1)
    _choice(p, "monthly_rounding", list(ROUNDING_METHODS), e, required=False)


def _v_arrears_policy(p, e):
    if not isinstance(p.get("enabled", True), bool):
        e.append("'enabled' must be true or false")
    _num(p, "lookback_months", e, required=False, minimum=1, maximum=36)


def _v_pay_cycle(p, e):
    mode = p.get("mode")
    if mode not in PAY_CYCLE_MODES:
        e.append(f"'mode' must be one of {', '.join(PAY_CYCLE_MODES)}")
        return
    if mode == "calendar":
        _num(p, "start_day", e, required=False, minimum=1, maximum=1)
        return
    day = _num(p, "start_day", e, minimum=2, maximum=28)
    if day is not None and day != day.to_integral_value():
        e.append("'start_day' must be a whole day of the month")


def _v_labour_codes(p, e):
    if p.get("apply") not in ("as_government", "not_applied"):
        e.append("'apply' must be 'as_government' or 'not_applied'")


def _v_cut_off(p, e):
    day = _num(p, "day", e, minimum=1, maximum=31)
    if day is not None and day != day.to_integral_value():
        e.append("'day' must be a whole day of the month")


def cut_off_date(versions: list[dict], period: Period):
    """The cut-off day of ``period``: the setting's day, or the month's last day if the month is shorter."""
    day = int(d(resolve_company(versions, "cut_off", period).get("day", 28)))
    return period.start.replace(day=min(day, period.end.day))


def _v_salary_structure(p, e):
    comps = p.get("components")
    if not isinstance(comps, list) or not comps:
        e.append("'components' must be a non-empty list")
        return
    remainder = [c for c in comps if c.get("share") == "remainder"]
    if len(remainder) != 1:
        e.append("exactly one component must have share \"remainder\"")
    total = Decimal("0")
    for c in comps:
        if c.get("share") != "remainder":
            total += d(c.get("share"))
    if total > 1:
        e.append("component shares add up to more than 100%")


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _v_work_calendar(p, e):
    off = p.get("weekly_off")
    if not isinstance(off, list) or any(day not in WEEKDAYS for day in off):
        e.append(f"'weekly_off' must be a list of day names ({', '.join(WEEKDAYS)})")
    elif len(off) > 6:
        e.append("'weekly_off' leaves no working day")
    holidays = p.get("holidays")
    if not isinstance(holidays, list):
        e.append("'holidays' must be a list of dates like 2025-08-15")
        return
    for day in holidays:
        try:
            ok = isinstance(day, str) and len(day) == 10 and parse_date(day) is not None
        except ValueError:
            ok = False
        if not ok:
            e.append(f"'{day}' in 'holidays' is not a date like 2025-08-15")


_VALIDATORS = {
    "pf": _v_pf,
    "esi": _v_esi,
    "professional_tax": _v_pt,
    "income_tax": _v_income_tax,
    "wage_definition": _v_wage_definition,
    "lop": _v_lop,
    "rounding": _v_rounding,
    "pf_policy": _v_pf_policy,
    "esi_policy": _v_esi_policy,
    "pt_policy": _v_pt_policy,
    "tds_policy": _v_tds_policy,
    "arrears_policy": _v_arrears_policy,
    "salary_structure": _v_salary_structure,
    "work_calendar": _v_work_calendar,
    "labour_codes": _v_labour_codes,
    "pay_cycle": _v_pay_cycle,
    "cut_off": _v_cut_off,
}


def is_statutory(key: str) -> bool:
    return key in STATUTORY_KEYS
