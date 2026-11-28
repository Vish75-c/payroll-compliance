"""Turn mapped tables (from any HR tool's screens or any spreadsheet) into one standard snapshot.

Both ways in (browser agent and spreadsheet upload) end here, so they produce exactly the
same shape of data. Every problem found becomes a check that the maker sees before the
month is calculated.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from payroll_engine.money import d, round_amount, to_str
from payroll_engine.periods import Period, Window, overlap_days

from .canonical import COMP_COMPONENTS, IGNORE, MONTH_TARGET_PREFIX, PART_TARGET_PREFIX, is_valid_target, target_info
from .values import (
    ValueError_,
    guess_approved,
    guess_half,
    guess_unpaid,
    is_blank,
    normalize_text,
    parse_amount,
    parse_bool,
    parse_date,
    parse_gender,
    parse_number,
    parse_period,
    parse_state,
)

SCHEMA_VERSION = 1


def validate_mapping(mapping: dict[str, str], headers: list[str]) -> list[str]:
    errors = []
    seen: dict[str, str] = {}
    for header, target in mapping.items():
        if header not in headers:
            errors.append(f"column '{header}' is not in the table")
        if not is_valid_target(target):
            errors.append(f"'{target}' is not a known field")
            continue
        if target == IGNORE or target.startswith(MONTH_TARGET_PREFIX):
            if target in seen and target != IGNORE:
                errors.append(f"columns '{seen[target]}' and '{header}' both map to {target}")
            seen.setdefault(target, header)
            continue
        if target in seen:
            errors.append(f"columns '{seen[target]}' and '{header}' both map to '{target_info(target)['label']}'")
        seen[target] = header
    targets = set(mapping.values())
    useful = targets - {IGNORE, "employee.code", "employee.name", "employee.first_name", "employee.last_name"}
    if useful and not ({"employee.code", "employee.name", "employee.first_name"} & targets):
        errors.append("map at least one column to Employee ID or Employee name, so rows can be matched to people")
    return errors


def default_value_maps(rows: list[dict], mapping: dict[str, str]) -> dict:
    """Suggest which leave types are unpaid and which statuses count, for a person to confirm."""
    by_target = {t: h for h, t in mapping.items()}
    out: dict[str, dict] = {}
    if "leave.type" in by_target:
        values = sorted({normalize_text(r.get(by_target["leave.type"], "")) for r in rows} - {""})
        out["leave.type"] = {v: ("unpaid" if guess_unpaid(v) else "paid") for v in values}
    if "leave.status" in by_target:
        values = sorted({normalize_text(r.get(by_target["leave.status"], "")) for r in rows} - {""})
        out["leave.status"] = {v: ("count" if guess_approved(v) else "skip") for v in values}
    breakdown = [by_target[t] for t in ("leave.start_breakdown", "leave.end_breakdown") if t in by_target]
    if breakdown:
        values = sorted({normalize_text(r.get(h, "")) for r in rows for h in breakdown} - {""})
        out["leave.breakdown"] = {v: ("half" if guess_half(v) else "full") for v in values}
    return out


def _without_hra(structure: dict) -> dict:
    """The salary structure for someone without HRA: HRA's share goes to the part that takes the rest."""
    comps = [dict(x) for x in structure["components"] if x["code"] != "hra"]
    if comps and not any(x["share"] == "remainder" for x in comps):
        target = next((x for x in comps if x["code"] == "special_allowance"), comps[-1])
        target["share"] = "remainder"
    return {**structure, "components": comps}


def _split_gross(gross: Decimal, structure: dict) -> dict[str, Decimal]:
    parts: dict[str, Decimal] = {}
    remainder_code = None
    for comp in structure["components"]:
        if comp["share"] == "remainder":
            remainder_code = comp["code"]
            continue
        parts[comp["code"]] = round_amount(gross * d(comp["share"]))
    parts[remainder_code] = gross - sum(parts.values(), Decimal(0))
    return parts


def _fill_remainder(given: dict[str, Decimal], gross: Decimal, structure: dict) -> dict[str, Decimal]:
    """Split the part of gross that the source does not itemise over the structure's missing parts."""
    remainder = gross - sum(given.values(), Decimal(0))
    missing = [comp for comp in structure["components"] if comp["code"] not in given]
    out = dict(given)
    if not missing:
        out["other_allowance"] = out.get("other_allowance", Decimal(0)) + remainder
        return out
    shares = {}
    for comp in missing:
        if comp["share"] == "remainder":
            fixed = sum((d(x["share"]) for x in structure["components"] if x["share"] != "remainder"), Decimal(0))
            shares[comp["code"]] = 1 - fixed
        else:
            shares[comp["code"]] = d(comp["share"])
    total_share = sum(shares.values(), Decimal(0)) or Decimal(1)
    allocated = Decimal(0)
    for comp in missing[:-1]:
        value = round_amount(remainder * shares[comp["code"]] / total_share)
        out[comp["code"]] = value
        allocated += value
    out[missing[-1]["code"]] = remainder - allocated
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class _Collector:
    def __init__(self):
        self.checks: list[dict] = []
        self.employees: dict[str, dict] = {}
        self.name_keys: dict[str, str] = {}  # normalised name -> key
        self.comp: list[dict] = []
        self.leave: list[dict] = []
        self.attendance: list[dict] = []
        self.presence: list[dict] = []  # a day someone checked in (daily attendance)
        self.presence_dates: set[str] = set()  # every date in the attendance read, whoever it belongs to
        self.untyped_leave: dict[str, int] = {}  # table -> leave rows with no type
        self.split_rows: dict[str, int] = {}  # table -> salary rows given only as a total, split by the structure
        self.work_calendar: dict = {"weekly_off": ["saturday", "sunday"], "holidays": []}  # to leave off-days out of a leave

    def check(self, level, code, message, **detail):
        self.checks.append({"level": level, "code": code, "message": message, **({"detail": detail} if detail else {})})


def revision_key(code: str, effective_from, components: dict) -> str:
    return json.dumps([code, effective_from, sorted(components.items())])


def build_snapshot(tables: list[dict], salary_structure: dict, first_seen: date,
                   seen_before: dict[str, str] | None = None, work_calendar: dict | None = None) -> tuple[dict, list[dict]]:
    """``tables``: [{"name", "headers", "rows", "mapping", "value_maps", "options", "read_info", "source_url"}].

    ``seen_before`` maps a salary record (see ``revision_key``) to the date an earlier read first
    saw it. A salary record without a "recorded on" date is treated as recorded when it was first
    seen, so a raise HR enters late is paid as arrears instead of rewriting months already paid.
    """
    seen_before = seen_before or {}
    c = _Collector()
    c.work_calendar = work_calendar or {"weekly_off": ["saturday", "sunday"], "holidays": []}
    roster = [t for t in tables if t.get("dataset_hint") == "employees" or _has_employee_attributes(t["mapping"])]
    roster_ids = {id(t) for t in (roster or tables)}
    for table in tables:
        _read_table(c, table, salary_structure, id(table) in roster_ids)

    # Resolve rows that identified people only by name.
    by_name = {}
    for key, emp in c.employees.items():
        if emp.get("name"):
            by_name.setdefault(_slug(emp["name"]), key)
    final_employees: dict[str, dict] = {}
    key_to_code: dict[str, str] = {}
    for key, emp in c.employees.items():
        if key.startswith("name:"):
            continue
        final_employees[key] = emp
        key_to_code[key] = key
    for key, emp in c.employees.items():
        if not key.startswith("name:"):
            continue
        slug = key[5:]
        match = next((k for k, e in final_employees.items() if _slug(e.get("name", "")) == slug), None)
        if match:
            for field, value in emp.items():
                final_employees[match].setdefault(field, value)
            key_to_code[key] = match
        else:
            code = f"N-{slug}"[:50]
            emp["code"] = code
            final_employees[code] = emp
            key_to_code[key] = code
            c.check("warning", "no_employee_id", f"{emp.get('name')} has no employee ID in the source; using '{code}'.")

    unknown: dict[str, dict[str, int]] = {}  # table -> person -> rows

    def resolve(row_key: str, source: str):
        if row_key in key_to_code:
            return key_to_code[row_key]
        if row_key.startswith("name:"):
            match = next((k for k, e in final_employees.items() if _slug(e.get("name", "")) == row_key[5:]), None)
            if match:
                return match
        table = source.split(" row ")[0]
        people = unknown.setdefault(table, {})
        who = row_key.replace("name:", "")
        people[who] = people.get(who, 0) + 1
        return None

    compensation, leave, attendance = [], [], []
    for row in c.comp:
        code = resolve(row.pop("_key"), row["source"])
        if code:
            compensation.append({"employee_code": code, **row})
    for row in c.leave:
        code = resolve(row.pop("_key"), row["source"])
        if code:
            leave.append({"employee_code": code, **row})
    for row in c.attendance:
        code = resolve(row.pop("_key"), row["source"])
        if code:
            attendance.append({"employee_code": code, **row})
    present: dict[tuple[str, str], set[int]] = {}  # (employee, month) -> days of the month checked in
    for row in c.presence:
        code = resolve(row.pop("_key"), row["source"])
        if code:
            present.setdefault((code, row["date"][:7]), set()).add(int(row["date"][8:10]))

    for name, rows in c.split_rows.items():
        parts = ", ".join(f"{x['code'].replace('_', ' ')} {x['share'] if x['share'] == 'remainder' else f'{float(x["share"]) * 100:g}%'}"
                          for x in salary_structure.get("components", []))
        c.check("info", "split_by_structure",
                f"{name}: {rows} salary record{'s give' if rows != 1 else ' gives'} only the total monthly salary, so "
                f"{'each was' if rows != 1 else 'it was'} split into parts by the company's salary structure ({parts}).")
    for name, rows in c.untyped_leave.items():
        c.check("warning", "leave_without_type",
                f"{name}: {rows} leave row{'s have' if rows != 1 else ' has'} no leave type, so {'they count' if rows != 1 else 'it counts'} as "
                "unpaid leave. Include the leave type (e.g. Sick Leave, Loss of Pay) so only unpaid leave cuts pay.")

    # Rows for people who are not in the employee list: one error per table, not one per row.
    for table, people in unknown.items():
        rows = sum(people.values())
        names = list(people)
        hint = ""
        if len(people) >= max(3, len(final_employees) // 2):
            hint = (" So many usually means the employee list is the wrong file or incomplete: check the file given as "
                    "Employees (or the employee screen) holds everyone.")
        c.check("error", "unknown_employee",
                f"{rows} {table} row{'s' if rows != 1 else ''} belong to {len(names)} {'people' if len(names) != 1 else 'person'} "
                f"who {'are' if len(names) != 1 else 'is'} not in the employee list ({', '.join(names[:8])}"
                f"{f' and {len(names) - 8} more' if len(names) > 8 else ''}).{hint}")

    compensation = _salary_history(c, compensation, first_seen, seen_before)
    no_back_pay = [r for r in compensation if r.get("no_arrears") and r.get("effective_from") and r.get("recorded_on")
                   and r["effective_from"] < r["recorded_on"][:7] + "-01"]
    if no_back_pay:
        c.check("info", "no_arrears", f"{len(no_back_pay)} salary revision{'s are' if len(no_back_pay) != 1 else ' is'} marked no arrears: "
                + "; ".join(f"{r['employee_code']} from {r['effective_from']} is paid from {r['recorded_on'][:7]}, when it was recorded"
                            for r in no_back_pay[:5]) + ". Months before are not paid again.")

    employees = []
    for code in sorted(final_employees):
        emp = final_employees[code]
        emp["code"] = code
        if not emp.get("name"):
            c.check("warning", "no_name", f"Employee {code} has no name.")
            emp["name"] = code
        employees.append({k: emp.get(k) for k in (
            "code", "name", "work_state", "gender", "date_of_joining", "date_of_exit", "pf_applicable", "esi_applicable",
            "department", "designation", "pay_group") + PAYMENT_FIELDS + EXIT_FIELDS if emp.get(k) not in (None, "")})

    _exits(c, tables, employees, final_employees)
    incomplete = _completeness(c, tables, employees, compensation, leave, attendance)
    carried, waits = _payment_and_returns(c, tables, employees, first_seen)
    for code, fields in waits.items():
        incomplete.setdefault(code, []).extend(f for f in fields if f not in incomplete.get(code, []))
    data = {
        "schema_version": SCHEMA_VERSION,
        "employees": employees,
        "compensation": sorted(compensation, key=lambda r: (r["employee_code"], r["effective_from"] or "", r["recorded_on"])),
        "leave": sorted(leave, key=lambda r: (r["employee_code"], r["start_date"], r["source"])),
        "attendance": sorted(attendance, key=lambda r: (r["employee_code"], r["period"])),
        **_presence(c, present),
        "sources": [
            {"table": t["name"], "rows": len(t["rows"]), "url": t.get("source_url", ""), "read_info": t.get("read_info", {})}
            for t in tables
        ],
    }
    if incomplete:  # who is missing which fact; only present when someone is, so complete data hashes as before
        data["incomplete"] = {code: incomplete[code] for code in sorted(incomplete)}
    if carried:  # which payment and return facts this source has a column for (absent: the source never gives them)
        data["fields_present"] = carried
    return data, c.checks


def _salary_history(c: _Collector, rows: list[dict], first_seen: date, seen_before: dict[str, str]) -> list[dict]:
    by_start: dict[tuple, list[dict]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["source"]):
        by_start[row["employee_code"], row["effective_from"]].append(row)

    history = []
    for (code, start), revisions in by_start.items():
        dated_amounts = {revision_key(code, start, r["components"]) for r in revisions if r["recorded_on"]}
        by_recorded = {}
        undated = [r for r in revisions if not r["recorded_on"] and revision_key(code, start, r["components"]) not in dated_amounts]
        for row in [r for r in revisions if r["recorded_on"]] + undated:
            key = revision_key(code, start, row["components"])
            recorded = row["recorded_on"] or seen_before.get(key, first_seen.isoformat())
            existing = by_recorded.get(recorded)
            if existing is None and not row["recorded_on"]:
                # A date inferred from the import cannot order two different salaries in the same read.
                existing = next((r for r in by_recorded.values() if r["components"] != row["components"]), None)
            if existing is not None:
                if existing["components"] != row["components"]:
                    c.check("error", "salary_conflict",
                            f"Two different salaries for {code} starting {start or 'with no date'} need distinct recording dates "
                            f"({existing['source']} and {row['source']}).")
                continue
            by_recorded[recorded] = dict(row, recorded_on_assumed=not row["recorded_on"], recorded_on=recorded)
        history.extend(by_recorded.values())
    return history


def _presence(c: _Collector, present: dict[tuple[str, str], set[int]]) -> dict:
    """Days each person checked in, compact: one row per person per month. Only there when attendance was read,
    so data without attendance looks exactly as before."""
    if not c.presence_dates:
        return {}
    first, last = min(c.presence_dates), max(c.presence_dates)
    people = len({code for code, _ in present})
    c.check("info", "attendance_range", f"Attendance covers {date.fromisoformat(first):%d %b %Y} to {date.fromisoformat(last):%d %b %Y}: "
            f"{sum(len(v) for v in present.values())} days present for {people} {'people' if people != 1 else 'person'}. "
            "Days with no check-in and no approved leave are suggested on the month page as possible unpaid absences.")
    return {"presence": [{"employee_code": code, "month": month, "days": sorted(days)} for (code, month), days in sorted(present.items())],
            "presence_range": {"from": first, "to": last}}


_NAME_TARGETS = {"employee.code", "employee.name", "employee.first_name", "employee.last_name"}
NAME_WITH_CODE = re.compile(r"(.+?)\s*[\(\[]\s*([A-Za-z0-9][A-Za-z0-9\-_/.]*)\s*[\)\]]")


def _has_employee_attributes(mapping: dict) -> bool:
    """A table is an employee list if it says more about people than their ID and name."""
    return any(t.startswith("employee.") and t not in _NAME_TARGETS for t in mapping.values())


def _read_table(c: _Collector, table: dict, structure: dict, roster: bool):
    mapping = {h: t for h, t in table["mapping"].items() if t != IGNORE}
    value_maps = table.get("value_maps") or {}
    order = (table.get("options") or {}).get("date_order", "DMY")
    by_target: dict[str, str] = {t: h for h, t in mapping.items()}
    month_cols = {h: t.split("@", 1)[1] for h, t in mapping.items() if t.startswith(MONTH_TARGET_PREFIX)}

    def parse(kind, raw, where):
        try:
            if kind == "amount":
                return to_str(parse_amount(raw))
            if kind == "number":
                return to_str(parse_number(raw))
            if kind == "date":
                return parse_date(raw, order).isoformat()
            if kind == "bool":
                return parse_bool(raw)
            if kind == "state":
                return parse_state(raw)
            if kind == "gender":
                return parse_gender(raw)
            if kind == "period":
                return str(parse_period(raw))
            return normalize_text(raw)
        except ValueError_ as exc:
            c.check("error", "bad_value", f"{where}: {exc}")
            return None

    no_month: list[str] = []  # rows of unpaid days with no month in the row and none chosen for the table
    for index, row in enumerate(table["rows"], start=1):
        where = f"{table['name']} row {index}"
        values: dict[str, object] = {}
        for target, header in by_target.items():
            if target.startswith(MONTH_TARGET_PREFIX):
                continue
            raw = row.get(header)
            if is_blank(raw):
                continue
            parsed = parse(target_info(target)["kind"], raw, f"{where}, column '{header}'")
            if parsed is not None:
                values[target] = parsed
        name = values.get("employee.name")
        if not name and (values.get("employee.first_name") or values.get("employee.last_name")):
            name = " ".join(str(values.get(k, "")) for k in ("employee.first_name", "employee.last_name")).strip()
        code = values.get("employee.code")
        if name:
            # Many HR tools show "Priya Sharma (BL001)" in one column: split it.
            m = NAME_WITH_CODE.fullmatch(str(name))
            if m:
                name = m.group(1).strip()
                code = code or m.group(2)
        if code:
            # The same "Name (CODE)" cell mapped as the employee ID (an AI suggestion can pick either):
            # the ID is the part in brackets, so people match whichever way the column was mapped.
            m = NAME_WITH_CODE.fullmatch(str(code))
            if m:
                code = m.group(2)
                name = name or m.group(1).strip()
        if not code and not name:
            if any(not is_blank(v) for v in row.values()):
                c.check("warning", "row_without_employee", f"{where} has no employee ID or name and was skipped.")
            continue
        key = str(code) if code else f"name:{_slug(name)}"

        # employee fields
        emp_fields = {t.split(".", 1)[1]: v for t, v in values.items() if t.startswith("employee.") and t not in (
            "employee.code", "employee.first_name", "employee.last_name")}
        if name:
            emp_fields["name"] = name
        if roster:
            existing = c.employees.setdefault(key, {})
            for field, value in emp_fields.items():
                if existing.get(field) not in (None, "") and existing[field] != value and field not in ("department", "designation"):
                    c.check("warning", "employee_conflict", f"{where}: {field} for {key} is '{value}', but another row says '{existing[field]}'. Kept the first.")
                    continue
                existing.setdefault(field, value)
        # rows of salary and leave tables are matched to the employee list afterwards

        # salary
        comp_amounts = {t.split(".", 1)[1]: d(v) for t, v in values.items() if t.startswith("comp.") and t.split(".", 1)[1] in COMP_COMPONENTS}
        gross = values.get("comp.monthly_gross")
        annual = values.get("comp.annual_ctc")
        variable = d(values.get("comp.variable_annual") or 0)
        # Other parts of salary under their own names (LTA, meal coupon...): annual in a file that gives the annual CTC.
        parts = {t.split("@", 1)[1]: d(v) for t, v in values.items() if t.startswith(PART_TARGET_PREFIX) and d(v)}
        monthly_parts = {k: round_amount(v / 12) for k, v in parts.items()} if annual else parts
        row_structure = _without_hra(structure) if values.get("comp.hra_applicable") is False else structure
        if parts and not (comp_amounts or gross or annual):
            c.check("warning", "parts_without_salary", f"{where}: parts of salary ({', '.join(parts)}) are given without the salary; not used.")
        if variable and not annual:
            c.check("info", "variable_without_ctc", f"{where}: variable pay is given with a monthly salary, not an annual CTC, so it does "
                    "not change the monthly pay. Pay it as a bonus in the month it is due.")
        if comp_amounts or gross or annual:
            components = dict(comp_amounts)
            if not components:
                if gross:
                    monthly = d(gross) - sum(monthly_parts.values(), Decimal(0))
                else:  # the CTC less variable pay, a twelfth a month, less the parts that have their own names
                    monthly = round_amount((d(annual) - variable) / 12) - sum(monthly_parts.values(), Decimal(0))
                if monthly < 0:
                    c.check("error", "parts_more_than_salary", f"{where}: the parts of salary and variable pay add up to more than the salary.")
                    monthly = Decimal(0)
                components = _split_gross(monthly, row_structure)
                c.split_rows[table["name"]] = c.split_rows.get(table["name"], 0) + 1  # one summary check per table
            elif gross and sum(components.values()) < d(gross):
                components = _fill_remainder(components, d(gross), row_structure)
                c.check("info", "remainder_split",
                        f"{where}: the listed parts add up to less than the monthly salary {gross}; the rest was split as "
                        + ", ".join(f"{k} {to_str(v)}" for k, v in components.items() if k not in comp_amounts) + " by the company's salary structure.")
            elif gross and sum(components.values()) > d(gross):
                c.check("warning", "components_vs_gross",
                        f"{where}: parts of pay add up to {to_str(sum(components.values()))}, more than the gross {gross}. The parts were used.")
            if values.get("comp.hra_applicable") is False and components.get("hra"):
                c.check("warning", "hra_not_applicable", f"{where}: HRA is given ({to_str(components['hra'])}) but marked not applicable. The HRA given was used.")
            for code, amount in monthly_parts.items():
                components[code] = components.get(code, Decimal(0)) + amount
            c.comp.append({
                "_key": key,
                "effective_from": values.get("comp.effective_from"),
                "recorded_on": values.get("comp.recorded_on"),
                "recorded_on_assumed": False,
                "components": {k: to_str(v) for k, v in components.items()},
                "source": where,
                **({"variable_annual": to_str(variable)} if variable and annual else {}),
                **({"no_arrears": True} if values.get("comp.arrears") is False else {}),
                **({"nps_percent": to_str(d(values["comp.nps_percent"]))} if values.get("comp.nps_percent") not in (None, "") else {}),
            })

        # a raise given on the same row ("New salary", "Effective from")
        rev_amounts = {t.split(".", 1)[1]: d(v) for t, v in values.items() if t.startswith("revision.") and t.split(".", 1)[1] in COMP_COMPONENTS}
        rev_gross = values.get("revision.monthly_gross")
        if rev_amounts or rev_gross:
            start = values.get("revision.effective_from")
            if not start:
                c.check("error", "revision_without_date", f"{where}: a revised salary is given without the date it starts.")
            else:
                components = rev_amounts or _split_gross(d(rev_gross), structure)
                recorded = values.get("revision.recorded_on")
                c.comp.append({
                    "_key": key,
                    "effective_from": start,
                    "recorded_on": recorded,
                    "recorded_on_assumed": not recorded,
                    "components": {k: to_str(v) for k, v in components.items()},
                    "source": where + " (revised salary)",
                })

        # leave
        start = values.get("leave.start_date") or values.get("leave.date")
        if start and any(t in values for t in ("leave.type", "leave.days", "leave.unpaid", "leave.end_date")):
            leave_type = str(values.get("leave.type", ""))
            status = str(values.get("leave.status", ""))
            if "leave.status" in values:
                decision = (value_maps.get("leave.status") or {}).get(status)
                counts = decision == "count" if decision else guess_approved(status)
                if not counts:
                    continue
            if "leave.unpaid" in values:
                unpaid = bool(values["leave.unpaid"])
            elif leave_type:
                decision = (value_maps.get("leave.type") or {}).get(leave_type)
                unpaid = decision == "unpaid" if decision else guess_unpaid(leave_type)
            else:
                unpaid = True  # a leave table without a type is treated as unpaid leave; shown as a check (one per table)
                c.untyped_leave[table["name"]] = c.untyped_leave.get(table["name"], 0) + 1
            end = values.get("leave.end_date") or start
            if end < start:
                c.check("error", "leave_dates", f"{where}: leave ends ({end}) before it starts ({start}).")
                continue
            calendar_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            days = values.get("leave.days")
            if days is not None and d(days) > calendar_days:
                c.check("warning", "leave_days", f"{where}: {days} days of leave between {start} and {end} ({calendar_days} calendar days).")
            day_values = _day_values(c, values, value_maps, start, end, leave_type, days, where)
            c.leave.append({
                "_key": key,
                "start_date": start,
                "end_date": end,
                "days": days if days is not None else (to_str(sum((x["value"] for x in day_values), Decimal(0))) if day_values else str(calendar_days)),
                **({"day_values": [{"date": x["date"], "value": to_str(x["value"])} for x in day_values]} if day_values else {}),
                "leave_type": leave_type,
                "status": status,
                "unpaid": unpaid,
                "source": where,
            })

        # daily attendance: a day this person checked in
        if "presence.date" in values:
            c.presence.append({"_key": key, "date": values["presence.date"], "source": where})
            c.presence_dates.add(values["presence.date"])

        # unpaid days given per month
        if "attendance.unpaid_days" in values:
            period = values.get("attendance.period") or (table.get("read_info") or {}).get("month")
            if not period:
                no_month.append(where)
            else:
                c.attendance.append({"_key": key, "period": period, "unpaid_days": values["attendance.unpaid_days"], "source": where})
        for header, period in month_cols.items():
            raw = row.get(header)
            if is_blank(raw):
                continue
            parsed = parse("number", raw, f"{where}, column '{header}'")
            if parsed is not None:
                c.attendance.append({"_key": key, "period": period, "unpaid_days": parsed, "source": f"{where}, column '{header}'"})
    if no_month:
        # One message for the table, not one per row: the file has no month (e.g. an "Employee LOP" export).
        c.check("error", "attendance_without_month",
                f"{table['name']}: {len(no_month)} row{'s' if len(no_month) != 1 else ''} of unpaid days but no month, in the file "
                "or chosen for it. Choose the payroll month these unpaid days are for on this table's field mapping.",
                table=table["name"], rows=len(no_month))


# --------------------------------------------------------------------------- leaving

# The PF return's exit reason codes (EPFO): C cessation (short service), S superannuation, R retirement,
# D death in service, P permanent and total disablement.
EXIT_CODES = {"C": "left the job", "S": "superannuation", "R": "retirement", "D": "death in service", "P": "permanent disablement"}
EXIT_WORDS = [("D", ("death", "deceased", "expired", "died")), ("P", ("disablement", "disabled", "disability")),
              ("S", ("superannuation",)), ("R", ("retired", "retirement")),
              ("C", ("resign", "terminat", "contract expiry", "contract end", "abscond", "awol", "transfer", "dismiss", "cessation",
                     "short service", "left", "quit", "sick", "deported", "others", "other"))]


def exit_code(text) -> str | None:
    """'Resigned' -> C, 'Retired' -> R. None when the text names several kinds (a template's list of choices) or none."""
    norm = str(text or "").lower()
    found = {code for code, words in EXIT_WORDS if any(w in norm for w in words)}
    return found.pop() if len(found) == 1 else None


def _exits(c: _Collector, tables, employees: list[dict], raw: dict[str, dict]):
    """A leaving date counts only once the person has left; why they left becomes the PF exit reason code."""
    mapped = {t for tb in tables for t in (tb.get("mapping") or {}).values()}
    for emp in employees:
        source = raw.get(emp["code"], {})
        given = emp.pop("exit_reason", None)  # the words as written; only the PF code is kept
        if source.get("has_left") is False and emp.get("date_of_exit"):
            emp["planned_exit"] = emp.pop("date_of_exit")
            c.check("info", "planned_exit", f"{emp['code']}: leaving on {emp['planned_exit']} is planned, not done (has left: no), "
                    "so pay does not stop. When they leave, mark it in the HR tool and read again.", employee=emp["code"])
        if not emp.get("date_of_exit"):
            continue
        code = "D" if source.get("deceased") else exit_code(given) if given else None
        if code:
            emp["exit_reason"] = code
        elif given:
            c.check("warning", "exit_reason_unclear", f"{emp['code']}: '{str(given)[:60]}' does not say clearly why they left "
                    "(resigned, retired, superannuation, death or disablement), so no PF exit reason was set.", employee=emp["code"])
    if "employee.exit_reason" in mapped or "employee.deceased" in mapped:
        missing = [e["code"] for e in employees if e.get("date_of_exit") and not e.get("exit_reason")]
        if missing:
            c.check("warning", "no_exit_reason", f"{len(missing)} {'people' if len(missing) > 1 else 'person'} who left "
                    f"{'have' if len(missing) > 1 else 'has'} no clear reason for leaving ({', '.join(missing[:5])}"
                    f"{' and more' if len(missing) > 5 else ''}). The PF return needs one for each person who left.", employees=missing)


# --------------------------------------------------------------------------- paying people and filing returns

EXIT_FIELDS = ("exit_reason", "planned_exit")
PAYMENT_FIELDS = ("pan", "bank_account", "ifsc", "name_at_bank", "payment_mode", "uan", "pf_number", "pf_joining_date", "esi_number",
                  "date_of_birth", "pf_full_wage", "eps_full_wage", "eps_member", "international_worker", "lwf_covered", "pran")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
UAN_RE = re.compile(r"^[0-9]{12}$")
ESI_RE = re.compile(r"^[0-9]{10}$")
ACCOUNT_RE = re.compile(r"^[A-Z0-9]{6,20}$")
PAYMENT_WORDS = {"bank": ("bank", "transfer", "neft", "rtgs", "imps", "online", "account credit", "direct deposit"),
                 "cheque": ("cheque", "check"), "cash": ("cash",), "dd": ("demand draft", "dd")}


def payment_mode(text: str) -> str | None:
    """'Bank Transfer', 'NEFT' -> bank; 'Cheque' -> cheque. None when unclear (several modes, or none)."""
    norm = re.sub(r"[^a-z ]+", " ", str(text).lower())
    found = {mode for mode, words in PAYMENT_WORDS.items() if any(re.search(rf"\b{w}\b", norm) for w in words)}
    return found.pop() if len(found) == 1 else None


def _payment_and_returns(c: _Collector, tables, employees: list[dict], today: date) -> tuple[list[str], dict[str, list[str]]]:
    """Check what paying people and filing the returns need, but only for the facts this HR data has a column for: a
    source that never gives PANs is noted once, without stopping anyone. Returns (facts present, who waits for what).

    Only a missing bank account (for someone paid by bank transfer) makes a person wait: there is nothing to pay into.
    A missing PAN does not stop pay; TDS is then at least 20%. A missing UAN, ESI number or date of birth is a warning:
    pay can be worked out, and new joiners often get a UAN after their first month."""
    mapped = {t.split(".", 1)[1] for tb in tables for t in (tb.get("mapping") or {}).values() if str(t).startswith("employee.")}
    present = [f for f in PAYMENT_FIELDS if f in mapped]
    waits: dict[str, list[str]] = {}
    if not employees:
        return present, waits

    def some(codes):
        return ", ".join(codes[:5]) + (f" and {len(codes) - 5} more" if len(codes) > 5 else "")

    def clean(emp, field, pattern):
        """Upper-case and strip spaces; a value that is not in the right format is dropped (and counted as missing)."""
        if not emp.get(field):
            return False
        value = re.sub(r"[\s-]", "", str(emp[field])).upper()
        if pattern.match(value):
            emp[field] = value
            return True
        emp.pop(field)
        return None  # given, but not valid

    def report(field, label, people, invalid, level, why):
        if not people:
            return
        bad = f" ({len(invalid)} given but not in the right format: {some(invalid)})" if invalid else ""
        c.check(level, f"no_{field}", f"{len(people)} of {len(employees)} employees have no valid {label} ({some(people)}){bad}. {why}",
                employees=people)

    for emp in employees:
        if "payment_mode" in emp:
            mode = payment_mode(emp["payment_mode"])
            if mode is None:
                c.check("warning", "payment_mode_unclear", f"{emp['code']}: payment mode '{emp['payment_mode'][:40]}' is not clear; "
                        "treated as bank transfer.", employee=emp["code"])
            emp["payment_mode"] = mode or "bank"

    if "pan" in present:
        missing, invalid = [], []
        for emp in employees:
            ok = clean(emp, "pan", PAN_RE)
            if not ok:
                missing.append(emp["code"])
                if ok is None:
                    invalid.append(emp["code"])
        report("pan", "PAN", missing, invalid, "warning", "They are paid, but TDS is at least 20% (section 206AA) until HR adds a valid PAN.")
    else:
        c.check("info", "pan_not_in_data", "This HR data has no PAN column. PANs are needed for the TDS return and Form 16.")

    if "bank_account" in present or "ifsc" in present:
        by_transfer = [e for e in employees if e.get("payment_mode", "bank") == "bank"]
        no_account, no_ifsc, bad_ifsc = [], [], []
        for emp in by_transfer:
            if not clean(emp, "bank_account", ACCOUNT_RE):
                no_account.append(emp["code"])
            ok = clean(emp, "ifsc", IFSC_RE)
            if not ok:
                no_ifsc.append(emp["code"])
                if ok is None:
                    bad_ifsc.append(emp["code"])
        for field, label, people, invalid in (("bank_account", "bank account number", no_account, []),
                                              ("ifsc", "IFSC", no_ifsc, bad_ifsc)):
            if not people:
                continue
            if len(people) == len(by_transfer):  # nobody has it: a column is missing or mapped wrongly
                report(field, label, people, invalid, "error", "Paid by bank transfer, so the salary has nowhere to go. "
                       "Check that the column is read and mapped, or add it in the HR tool, and read again.")
                continue
            for code in people:
                waits.setdefault(code, []).append(field)
            report(field, label, people, invalid, "warning", "Paid by bank transfer, so they wait (left out of a month) until HR "
                   "adds it, and are put on hold at the cut-off if it is still missing; everyone else is paid as usual.")
    else:
        c.check("info", "bank_not_in_data", "This HR data has no bank account column, so a bank transfer file cannot be made from it.")

    pf_members = [e for e in employees if e.get("pf_applicable", True) is not False]
    if "uan" in present:
        missing = [e["code"] for e in pf_members if not clean(e, "uan", UAN_RE)]
        report("uan", "UAN", missing, [], "warning", "PF is worked out as usual; the PF return needs a UAN for each member.")
    if "esi_number" in present:
        missing = [e["code"] for e in employees if e.get("esi_applicable", True) is not False and not clean(e, "esi_number", ESI_RE)]
        report("esi_number", "ESI number", missing, [], "info", "Needed in the ESI return for the people ESI covers that month.")
    if "date_of_birth" in present:
        missing = [e["code"] for e in pf_members if not e.get("date_of_birth")]
        report("date_of_birth", "date of birth", missing, [], "warning", "The pension share (EPS) stops at 58; without the date it cannot be checked.")
        over = [e["code"] for e in pf_members if e.get("date_of_birth") and _age(e["date_of_birth"], today) >= 58]
        if over:
            c.check("info", "eps_age", f"{len(over)} PF member{'s are' if len(over) > 1 else ' is'} 58 or older ({some(over)}): "
                    "none of the company's PF goes to the pension scheme for them.", employees=over)
    return present, waits


def _age(dob: str, on: date) -> int:
    born = date.fromisoformat(dob)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _day_values(c: _Collector, values: dict, value_maps: dict, start: str, end: str, leave_type: str, days, where: str) -> list[dict]:
    """How much of each date a leave takes, the way Horilla counts it: every day 1, and the first or last day 0.5 when
    it is a half (First Half / Second Half); a one-day leave is 0.5 if either side is a half. Returned only when it
    differs from "every calendar day is a full day", so months split by date instead of by share. Empty when the
    file's own number of days cannot be matched (the old split by share is used, with a warning)."""
    def half(field):
        raw = values.get(field)
        if raw in (None, ""):
            return False
        decision = (value_maps.get("leave.breakdown") or {}).get(normalize_text(raw))
        return decision == "half" if decision else guess_half(raw)

    first, last = date.fromisoformat(start), date.fromisoformat(end)
    dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    out = [{"date": x.isoformat(), "value": Decimal(1)} for x in dates]
    start_half, end_half = half("leave.start_breakdown"), half("leave.end_breakdown")
    if len(out) == 1:
        if start_half or end_half or values.get("leave.half_day") is True or ("half" in leave_type.lower() and not days):
            out[0]["value"] = Decimal("0.5")
    else:
        if start_half:
            out[0]["value"] = Decimal("0.5")
        if end_half:
            out[-1]["value"] = Decimal("0.5")
    total = sum((x["value"] for x in out), Decimal(0))
    given = d(days) if days not in (None, "") else total
    if given < total:
        # The HR tool left out weekly offs or holidays (Horilla does for some leave types): leave them out here too.
        off = {WEEKDAY_NAMES.index(w) for w in c.work_calendar.get("weekly_off", []) if w in WEEKDAY_NAMES}
        holidays = set(c.work_calendar.get("holidays", []))
        kept = [x for x in out if date.fromisoformat(x["date"]).weekday() not in off and x["date"] not in holidays]
        if sum((x["value"] for x in kept), Decimal(0)) == given:
            out, total = kept, given
    if given != total:
        c.check("warning", "leave_days_by_date", f"{where}: {to_str(given)} days of leave from {start} to {end} could not be "
                f"matched to dates; a leave across two months is split between them by its share of the days.")
        return []
    if all(x["value"] == 1 for x in out) and len(out) == len(dates):
        return []  # every calendar day a full day: splitting by share gives the same
    return out


def _completeness(c: _Collector, tables, employees, compensation, leave, attendance):
    for t in tables:
        # Every column that does not feed pay, so a person can see nothing important (a PAN, a bank account) was dropped.
        unused = [h for h in t["headers"] if (t.get("mapping") or {}).get(h, IGNORE) == IGNORE]
        if unused:
            shown = ", ".join(unused[:12]) + (f" and {len(unused) - 12} more" if len(unused) > 12 else "")
            c.check("info", "columns_not_used", f"{t['name']}: {len(unused)} of {len(t['headers'])} columns are not used for pay: {shown}.",
                    table=t["name"], columns=unused)
        notes = (t.get("read_info") or {}).get("notes_skipped") or []
        if notes:
            c.check("info", "notes_skipped", f"{t['name']}: {len(notes)} line{'s' if len(notes) != 1 else ''} of text under the data "
                    f"{'were' if len(notes) != 1 else 'was'} not read as people (\"{notes[0][:80]}\"…).", table=t["name"])
    for t in tables:
        info = t.get("read_info") or {}
        shown = info.get("total_shown")
        read = len(t["rows"])
        if shown is not None:
            if int(shown) == read:
                c.check("ok", "count_matches", f"{t['name']}: read {read} rows; the screen says there are {shown}.")
            else:
                c.check("error", "count_mismatch", f"{t['name']}: read {read} rows, but the screen says there are {shown}. Something was not read.")
        if info.get("complete") is False:
            c.check("error", "incomplete_read", f"{t['name']}: reading stopped early ({info.get('stopped_because', 'unknown reason')}).")
        for issue in info.get("issues", []):
            c.check(issue.get("level", "warning"), issue.get("code", "read_issue"), f"{t['name']}: {issue['message']}")

    # Data can come as separate files or screens; say when a kind of data is missing or in the wrong place.
    expected = {"compensation": ("salary", {"comp", "revision"}), "leave": ("leave", {"leave", "attendance"}),
                "attendance": ("attendance", {"presence", "attendance"})}
    mapped_groups = set()
    for t in tables:
        groups = {target_info(v)["group"] for v in (t.get("mapping") or {}).values()}
        mapped_groups |= groups
        if t.get("dataset_hint") == "employees" and not _has_employee_attributes(t.get("mapping") or {}) and groups & {"comp", "revision", "leave", "attendance", "presence"}:
            kind = "attendance" if "presence" in groups else "leave" if groups & {"leave", "attendance"} else "salary"
            c.check("warning", "wrong_kind_of_data",
                    f"{t['name']}: given as the employee list, but its columns are {kind} data. Upload the employee list as Employees.")
        label, wanted = expected.get(t.get("dataset_hint") or "", (None, None))
        if label and not groups & wanted:
            c.check("warning", "wrong_kind_of_data",
                    f"{t['name']}: given as {label} data, but none of its columns is mapped to a {label} field. Check the file and its mapping.")
    if not mapped_groups & {"leave", "attendance"}:
        c.check("warning", "no_leave_data",
                "No leave was read, so nobody has unpaid days in this data. If leave is kept in a separate file, add it to this read before building the snapshot.")

    codes = {e["code"] for e in employees}
    with_salary = {r["employee_code"] for r in compensation}
    if not employees:
        c.check("error", "no_employees", "No employees were found.")

    def some(codes):
        return ", ".join(codes[:5]) + (f" and {len(codes) - 5} more" if len(codes) > 5 else "")

    # Facts that pay depends on. Missing for some people: those people wait (they are left out of a month until HR
    # adds the fact, or put on hold at the cut-off) and everyone else is paid. Missing for everyone: a column is
    # missing or mapped wrongly, which is not one person's gap, so the data is stopped. Nothing corrects a paid month,
    # so nobody is ever paid on a guess.
    incomplete: dict[str, list[str]] = {}

    def gap(field: str, code: str, people: list[str], what: str, why: str):
        if not people:
            return
        if len(people) == len(employees):
            c.check("error", code, f"{len(people)} of {len(employees)} employees have no {what} ({some(people)}). {why} "
                    "Check that the column is read and mapped, or add it in the HR tool, and read again.")
            return
        for person in people:
            incomplete.setdefault(person, []).append(field)
        c.check("warning", code, f"{len(people)} of {len(employees)} employees have no {what} ({some(people)}). {why} "
                f"{'They are' if len(people) > 1 else 'This person is'} left out of a month until HR adds it, and put on hold "
                "at the cut-off if it is still missing; everyone else is paid as usual.", employees=people)

    gap("salary", "no_salary", sorted(codes - with_salary), "salary", "There is nothing to pay them from.")
    if employees and codes <= with_salary:
        c.check("ok", "salary_for_all", f"Every one of the {len(employees)} employees has a salary.")
    gap("date_of_joining", "no_joining_date", [e["code"] for e in employees if not e.get("date_of_joining")], "date of joining",
        "Someone who joined during a month would be paid for the whole month.")
    gap("work_state", "no_work_state", [e["code"] for e in employees if not e.get("work_state")], "work location",
        "Their professional tax cannot be worked out for the right state.")
    gap("gender", "no_gender", [e["code"] for e in employees if not e.get("gender")], "gender",
        "Professional tax depends on it in some states (Maharashtra, for example).")

    by_emp = defaultdict(list)
    for r in compensation:
        by_emp[r["employee_code"]].append(r)
    for code, rows in by_emp.items():
        rows = sorted(rows, key=lambda x: x["effective_from"] or "")
        for r in rows[1:]:
            if r.get("recorded_on_assumed"):
                c.check("info", "recorded_on_assumed",
                        f"Salary change for {code} from {r['effective_from'] or 'an unknown date'} has no 'recorded on' date; treated as recorded on "
                        f"{r['recorded_on']}, when it was first read. Months already paid before then keep the old salary; the difference is paid as arrears.")
        if len(rows) > 2 and all(r.get("recorded_on_assumed") for r in rows):
            c.check("warning", "revisions_without_recorded_date",
                    f"{code} has {len(rows)} salary records and none has a 'recorded on' date. When re-creating months before "
                    f"{rows[-1]['recorded_on']}, only the earliest record is used. Add a 'recorded on' column if you need older history.")

    unpaid = [r for r in leave if r["unpaid"]]
    paid = [r for r in leave if not r["unpaid"]]
    if leave:
        types_unpaid = sorted({r["leave_type"] or "(no type)" for r in unpaid})
        types_paid = sorted({r["leave_type"] or "(no type)" for r in paid})
        c.check("info", "leave_types",
                f"Counted as unpaid: {', '.join(types_unpaid) or 'none'}. Not unpaid (no pay cut): {', '.join(types_paid) or 'none'}.")
    periods = sorted({str(p) for r in unpaid for p in _periods_between(r["start_date"], r["end_date"])} | {r["period"] for r in attendance})
    for p in periods:
        total = sum((unpaid_days_for(data_stub(leave, attendance), code, Period.parse(p)) for code in codes), Decimal(0))
        c.check("info", "unpaid_days_total", f"{Period.parse(p).label}: {to_str(total)} unpaid days in total across all employees.")
    c.check("info", "summary", f"{len(employees)} employees, {len(compensation)} salary records, {len(leave)} leave records, {len(attendance)} monthly unpaid-day records.")
    return incomplete


def data_stub(leave, attendance):
    return {"leave": leave, "attendance": attendance}


def _periods_between(start: str, end: str):
    p = Period.parse(start[:7])
    last = Period.parse(end[:7])
    while p <= last:
        yield p
        p = p.next()


def unpaid_days_for(data: dict, code: str, period: Period, window: Window | None = None) -> Decimal:
    """Unpaid days of one employee in one month.

    A per-month figure ("attendance") wins when the source gives one; otherwise unpaid leave
    records are added up. Leave that spans months is split by calendar days. ``window`` is the
    attendance cycle of the month when it is not the calendar month (e.g. 26 Oct to 25 Nov).
    """
    explicit = [r for r in data.get("attendance", []) if r["employee_code"] == code and r["period"] == str(period)]
    if explicit:
        return sum((d(r["unpaid_days"]) for r in explicit), Decimal(0))
    total = Decimal(0)
    window = window or period
    for r in data.get("leave", []):
        if r["employee_code"] != code or not r["unpaid"]:
            continue
        if r.get("day_values"):  # a half day stays on its own date
            total += sum((d(x["value"]) for x in r["day_values"] if window.start <= date.fromisoformat(x["date"]) <= window.end), Decimal(0))
            continue
        start, end = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        inside = overlap_days(start, end, window)
        if not inside:
            continue
        span = (end - start).days + 1
        days = d(r["days"])
        total += days if inside == span else days * inside / span
    return total
