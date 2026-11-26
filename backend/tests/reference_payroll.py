"""A second, separate payroll calculator, used only by the tests to check the engine's amounts.

It is written from the rules as the guide states them and does NOT import ``payroll_engine`` or ``payroll``.
It reads the HR facts straight from the seed data (not from what the system imported) and the numbers of
each rule (rates, ceilings, slabs, due days) from ``data/rules/statutory_in.json``; the logic is its own.

What it covers (company choices of answer sheet A):
* salary in force each day of the month (a raise starting mid-month is paid by calendar days), and
  only salary records HR had recorded by the month's knowledge date;
* nobody is paid for a month that starts after their last working day;
* days before joining, days after the last working day and unpaid leave cut pay: monthly rate x days / the month's day count, each
  component rounded to the rupee (half up), never more than the component;
* arrears: every paid month in the last 12 is worked out again with the salary known now, its own
  unpaid days, day count and rules; the difference (and the PF on it) is paid now;
* PF 12% + 12% on basic (+ Labour Codes add-back once in force), capped at 15,000; EPS 8.33% of
  the capped wage; ESI 0.75% / 3.25% rounded up when gross is 21,000 or less; professional tax by
  the state's slabs (gender, February); TDS under the new regime: pay so far + this month + the
  salary in force for the rest of the year, less 75,000, slabs, rebate up to 12 lakh with marginal
  relief, 4% cess, the balance over the tax already deducted spread over the months left (up to the month of
  leaving, for someone who leaves before March).
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ZERO = Decimal(0)
STATES = {"Karnataka": "KA", "Maharashtra": "MH", "Tamil Nadu": "TN", "Telangana": "TG", "Delhi": "DL"}


def rupee(x: Decimal) -> Decimal:
    return Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def rupee_up(x: Decimal) -> Decimal:
    return Decimal(x).quantize(Decimal(1), rounding=ROUND_CEILING)


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    @staticmethod
    def of(text: str) -> "Month":
        y, m = text.split("-")
        return Month(int(y), int(m))

    def __str__(self):
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        return date(self.year, self.month, self.days)

    def next(self) -> "Month":
        return Month(self.year + (self.month == 12), 1 if self.month == 12 else self.month + 1)

    @property
    def fy(self) -> int:
        return self.year if self.month >= 4 else self.year - 1

    @property
    def march(self) -> "Month":
        return Month(self.fy + 1, 3)

    def months_to(self, other: "Month") -> int:
        return (other.year - self.year) * 12 + other.month - self.month

    def each_day(self):
        day = self.start
        while day <= self.end:
            yield day
            day += timedelta(days=1)


# --------------------------------------------------------------------------- facts from the seed

@dataclass
class Person:
    code: str
    gender: str
    joined: date
    state: str
    salaries: list[dict]  # {"from": date, "recorded": date | None, "c": {component: Decimal}}
    leave: list[dict] = field(default_factory=list)  # unpaid leave only: {"from", "to", "days"}
    left: date | None = None  # last working day


def people_from_seed(seed: dict) -> dict[str, Person]:
    out = {}
    for e in seed["employees"]:
        state = STATES[e["location"].split(",")[-1].strip()]
        out[e["code"]] = Person(e["code"], e["gender"], date.fromisoformat(e["doj"]), state, [],
                                left=date.fromisoformat(e["doe"]) if e.get("doe") else None)
    for s in seed["salaries"]:
        out[s["code"]].salaries.append({
            "from": date.fromisoformat(s["effective_from"]),
            "recorded": date.fromisoformat(s["recorded_on"]) if s.get("recorded_on") else None,
            "c": {"basic": Decimal(s["basic"]), "hra": Decimal(s["hra"]), "special_allowance": Decimal(s["special"])},
        })
    for lv in seed["leave"]:
        # Unpaid = Loss of Pay that was approved. Sick, casual and earned leave are paid; rejected or
        # pending requests are not leave at all.
        if lv["type"] == "Loss of Pay" and lv["status"] == "Approved":
            out[lv["code"]].leave.append({"from": date.fromisoformat(lv["from"]), "to": date.fromisoformat(lv["to"]),
                                          "days": Decimal(str(lv["days"]))})
    return out


# --------------------------------------------------------------------------- rules

class Rules:
    def __init__(self, path: Path | None = None):
        self.versions = json.loads((path or REPO / "data/rules/statutory_in.json").read_text())["versions"]

    def get(self, key: str, month: Month, where: str = "IN") -> dict | None:
        found = [v for v in self.versions if v["key"] == key and v["jurisdiction"] == where
                 and date.fromisoformat(v["effective_from"]) <= month.start]
        return max(found, key=lambda v: v["effective_from"])["payload"] if found else None


def pt_amount(slabs: list[dict], basis: Decimal, month: int, gender: str) -> Decimal:
    best, best_key = None, None
    for s in slabs:
        if s.get("gender") and s["gender"] != gender:
            continue
        if s.get("months") and month not in s["months"]:
            continue
        lower = Decimal(s["min"]) if "min" in s else Decimal(s["above"])
        if ("min" in s and basis < lower) or ("above" in s and basis <= lower):
            continue
        if s.get("max") is not None and basis > Decimal(s["max"]):
            continue
        key = ((2 if s.get("months") else 0) + (1 if s.get("gender") else 0), lower)
        if best_key is None or key > best_key:
            best, best_key = s, key
    if best is None:
        return ZERO
    return rupee(basis * Decimal(best["rate"])) if "rate" in best else Decimal(best["amount"])


def income_tax(taxable: Decimal, rule: dict) -> Decimal:
    taxable = max(ZERO, taxable)
    tax, lower = ZERO, ZERO
    for slab in rule["slabs"]:
        upper = None if slab["upto"] is None else Decimal(slab["upto"])
        if taxable > lower:
            top = taxable if upper is None else min(taxable, upper)
            tax += (top - lower) * Decimal(slab["rate"])
        if upper is None:
            break
        lower = upper
    limit, max_rebate = Decimal(rule["rebate"]["income_upto"]), Decimal(rule["rebate"]["max_rebate"])
    if taxable <= limit:
        tax -= min(tax, max_rebate)
    elif tax > taxable - limit:  # marginal relief: no more tax than the income above the limit
        tax = taxable - limit
    assert taxable <= Decimal("5000000"), "surcharge is not covered by the reference calculator"
    return rupee(tax * (1 + Decimal(rule["cess_rate"])))


# --------------------------------------------------------------------------- the calculator

@dataclass
class Paid:
    """What a final month paid one person (later arrears for that month are added on)."""
    earned: dict
    pf_employee: Decimal
    pf_employer: Decimal
    eps_employer: Decimal
    esi_covered: bool
    unpaid_input: Decimal
    day_count: int
    taxable_gross: Decimal
    tds: Decimal


class Reference:
    def __init__(self, people: dict[str, Person], rules: Rules | None = None, openings: dict | None = None,
                 company_day_count: int | None = None):
        self.people = people
        self.rules = rules or Rules()
        self.openings = openings or {}  # code -> (gross, tds) earlier in the financial year
        self.company_day_count = company_day_count
        self.paid: dict[Month, dict[str, Paid]] = {}

    # salary --------------------------------------------------------------
    @staticmethod
    def known(person: Person, knowledge: date) -> list[dict]:
        rows = [s for s in person.salaries if s["recorded"] is None or s["recorded"] <= knowledge]
        return sorted(rows or [min(person.salaries, key=lambda s: s["from"])],
                      key=lambda s: (s["from"], s["recorded"] or date.min))

    @staticmethod
    def earned(salaries: list[dict], month: Month, joined: date, unpaid_input: Decimal, day_count: int,
               left: date | None = None) -> tuple[dict, bool, Decimal]:
        """Compute the payable parts by enumerating calendar days and looking up each day's salary."""
        revisions = {s["from"]: s["c"] for s in salaries}
        first_day = max(month.start, joined)
        last_day = min(month.end, left or month.end)
        first_salary = min(revisions)
        on_roll: dict[str, Decimal] = {}
        off_roll: dict[str, Decimal] = {}
        for day in month.each_day():
            salary_day = max(first_day, min(day, last_day))
            effective = max((start for start in revisions if start <= salary_day), default=first_salary)
            totals = on_roll if first_day <= day <= last_day else off_roll
            for component, amount in revisions[effective].items():
                totals[component] = totals.get(component, ZERO) + amount

        employed = max(0, (last_day - first_day).days + 1)
        unpaid = min(unpaid_input, Decimal(employed))
        result = {}
        contracted = ZERO
        for component in on_roll.keys() | off_roll.keys():
            in_pay = on_roll.get(component, ZERO)
            out_pay = off_roll.get(component, ZERO)
            full_pay = rupee((in_pay + out_pay) / month.days)
            contracted += full_pay
            leave = unpaid * in_pay / employed if employed else ZERO
            deduction = full_pay if unpaid == employed else min(full_pay, rupee((out_pay + leave) / day_count))
            result[component] = full_pay - deduction
        return result, first_day <= last_day and first_salary > first_day, contracted

    def unpaid_leave(self, person: Person, month: Month) -> Decimal:
        total = ZERO
        for lv in person.leave:
            first, last = max(lv["from"], month.start), min(lv["to"], month.end)
            if first > last:
                continue
            inside, span = (last - first).days + 1, (lv["to"] - lv["from"]).days + 1
            total += lv["days"] if inside == span else lv["days"] * inside / span
        return total

    def pf(self, earned: dict, month: Month) -> tuple[Decimal, Decimal, Decimal]:
        rule = self.rules.get("pf", month)
        wage = sum((earned.get(c, ZERO) for c in rule["wage_components"]), ZERO)
        code = self.rules.get("wage_definition", month)
        if code and code["enabled"] and "pf" in code["applies_to"]:
            total = sum(earned.values(), ZERO)
            excluded = total - sum((earned.get(c, ZERO) for c in code["wage_components"]), ZERO)
            limit = total * Decimal(code["max_exclusion_share"])
            if excluded > limit:
                wage += excluded - limit
        base = min(wage, Decimal(rule["wage_ceiling"]))
        employee = rupee(base * Decimal(rule["employee_rate"]))
        employer = rupee(base * Decimal(rule["employer_rate"]))
        eps = min(rupee(min(wage, Decimal(rule["eps_wage_ceiling"])) * Decimal(rule["eps_rate"])), employer)
        return employee, employer, eps

    # one month -------------------------------------------------------------
    def month(self, period: str, day_count: int | None = None, one_time: dict | None = None,
              extra_unpaid: dict | None = None, knowledge: date | None = None) -> dict[str, dict]:
        m = Month.of(period)
        knowledge = knowledge or m.end
        days = day_count or self.company_day_count or m.days
        out = {}
        for p in self.people.values():
            if p.joined > m.end or (p.left is not None and p.left < m.start):
                continue
            salaries = self.known(p, knowledge)
            unpaid_input = min(self.unpaid_leave(p, m) + (extra_unpaid or {}).get(p.code, ZERO), Decimal(m.days))
            earned, _, contracted = self.earned(salaries, m, p.joined, unpaid_input, days, p.left)

            arrears = arrears_pf_emp = arrears_pf_er = arrears_eps = ZERO
            arrears_detail = {}
            for past in sorted(self.paid):
                if not past < m or past.months_to(m) > 12 or p.code not in self.paid[past]:
                    continue
                was = self.paid[past][p.code]
                again, missing, _ = self.earned(salaries, past, p.joined, was.unpaid_input, was.day_count, p.left)
                if missing:
                    continue  # no salary on record for that month: it cannot be worked out again
                deltas = {c: again.get(c, ZERO) - was.earned.get(c, ZERO) for c in set(again) | set(was.earned)}
                pe, pr, pension = self.pf(again, past)
                d_emp, d_er, d_eps = pe - was.pf_employee, pr - was.pf_employer, pension - was.eps_employer
                if not any(deltas.values()) and not d_emp and not d_er and not d_eps:
                    continue
                arrears += rupee(sum(deltas.values(), ZERO))
                arrears_pf_emp += d_emp
                arrears_pf_er += d_er
                arrears_eps += d_eps
                arrears_detail[past] = (deltas, d_emp, d_er, d_eps)

            extra = (one_time or {}).get(p.code, ZERO)
            regular = sum(earned.values(), ZERO)
            gross = regular + arrears + extra
            pf_emp, pf_er, eps = self.pf(earned, m)
            esi = self.rules.get("esi", m)
            first = Month(m.year, 4 if 4 <= m.month <= 9 else 10) if m.month >= 4 else Month(m.year - 1, 10)
            carried_coverage = any(paid[p.code].esi_covered for prior, paid in self.paid.items()
                                   if first <= prior < m and p.code in paid)
            covered = carried_coverage or ZERO < gross <= Decimal(esi["threshold"])
            exempt = contracted / 26 <= Decimal(esi.get("employee_daily_wage_exemption", "0"))
            esi_emp = rupee_up(gross * Decimal(esi["employee_rate"])) if covered and gross > ZERO and not exempt else ZERO
            esi_er = rupee_up(gross * Decimal(esi["employer_rate"])) if covered and gross > ZERO else ZERO
            pt_rule = self.rules.get("professional_tax", m, f"IN-{p.state}")
            pt = pt_amount(pt_rule["slabs"], gross, m.month, p.gender) if pt_rule else ZERO

            it = self.rules.get("income_tax", m)
            opening_gross, opening_tds = self.openings.get(p.code, (ZERO, ZERO))
            before = [self.paid[x][p.code] for x in self.paid if x < m and x.fy == m.fy and p.code in self.paid[x]]
            paid_so_far = opening_gross + sum((b.taxable_gross for b in before), ZERO)
            tds_so_far = opening_tds + sum((b.tds for b in before), ZERO)
            last = m.march
            if p.left is not None and Month(p.left.year, p.left.month) < last:
                last = max(Month(p.left.year, p.left.month), m)  # no pay is expected after the month of leaving
            future, f = ZERO, m.next()
            while f <= last:
                projected_earned, _, _ = self.earned(salaries, f, p.joined, ZERO, self.company_day_count or f.days, p.left)
                future += sum(projected_earned.values(), ZERO)
                f = f.next()
            projected = paid_so_far + gross + future
            annual = income_tax(projected - Decimal(it["standard_deduction"]), it)
            tds = max(ZERO, rupee((annual - tds_so_far) / (m.months_to(last) + 1)))

            pf_emp_total, pf_er_total = pf_emp + arrears_pf_emp, pf_er + arrears_pf_er
            eps_total = eps + arrears_eps
            deductions = pf_emp_total + esi_emp + pt + tds
            out[p.code] = {
                **{f"earned.{c}": v for c, v in earned.items()},
                "unpaid_days": unpaid_input, "arrears": arrears, "regular_gross": regular, "gross": gross,
                "pf_employee": pf_emp_total, "pf_employer": pf_er_total, "eps_employer": eps_total, "epf_employer": pf_er_total - eps_total,
                "esi_employee": esi_emp, "esi_employer": esi_er, "pt": pt, "tds": tds, "tds_paid_before": tds_so_far,
                "tds_projected_income": projected, "total_deductions": deductions, "net_pay": gross - deductions,
                "employer_cost": gross + pf_er_total + esi_er,
                "_paid": Paid(earned, pf_emp, pf_er, eps, covered, unpaid_input, days, gross, tds), "_arrears": arrears_detail,
            }
        return out

    def finalise(self, period: str, result: dict[str, dict]):
        """Record a month as paid, and add its arrears to the months they were for."""
        m = Month.of(period)
        self.paid[m] = {}
        for code, row in result.items():
            self.paid[m][code] = row["_paid"]
            for past, (deltas, d_emp, d_er, d_eps) in row["_arrears"].items():
                was = self.paid[past][code]
                for c, v in deltas.items():
                    was.earned[c] = was.earned.get(c, ZERO) + v
                was.pf_employee += d_emp
                was.pf_employer += d_er
                was.eps_employer += d_eps


def compare(engine_result: dict, reference: dict[str, dict]) -> list[str]:
    """Every amount the reference works out, for every person, against the engine. Empty means all equal."""
    problems = []
    engine = {e["code"]: e["amounts"] for e in engine_result["employees"]}
    if set(engine) != set(reference):
        problems.append(f"people differ: only engine {sorted(set(engine) - set(reference))}, "
                        f"only reference {sorted(set(reference) - set(engine))}")
    for code in sorted(set(engine) & set(reference)):
        for key, want in reference[code].items():
            if key.startswith("_"):
                continue
            got = Decimal(engine[code].get(key, "0"))
            if got != want:
                problems.append(f"{engine_result['period']} {code} {key}: engine {got}, reference {want}")
    return problems
