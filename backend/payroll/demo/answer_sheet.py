"""Run an answer-sheet case end to end and report which numbers match.

A case is a folder with ``choices.json`` (company settings and which months to run) and
``expected.csv``. The CSV may be "long" (one row per number: month, employee, item,
expected) or "wide" (one row per employee and month, one column per item). Column and
item names are matched by common aliases, so the official sheets can be dropped in as-is.

No AI is involved: instructions are read by the rule-based parser, and the comparison is
plain arithmetic.
"""
from __future__ import annotations

import csv
import json
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.test.utils import override_settings

from payroll_engine.money import d, to_str
from payroll_engine.periods import Period

from ..features.hr_data.values import MONTHS
from . import setup

ITEM_ALIASES = {
    "gross": ["gross", "gross pay", "gross salary", "total earnings", "gross earnings"],
    "net_pay": ["net", "net pay", "net salary", "take home", "net payable", "net amount"],
    "pf_employee": ["pf", "pf employee", "employee pf", "pf (employee)", "epf", "epf employee", "provident fund", "pf ee", "employee pf contribution"],
    "pf_employer": ["pf employer", "employer pf", "company pf", "pf (employer)", "pf er", "employer pf contribution"],
    "esi_employee": ["esi", "esi employee", "employee esi", "esi (employee)", "esi ee", "esic"],
    "esi_employer": ["esi employer", "employer esi", "company esi", "esi (employer)", "esi er"],
    "pt": ["pt", "professional tax", "prof tax", "p tax", "profession tax"],
    "tds": ["tds", "income tax", "tax", "income tax (tds)"],
    "lop_deduction": ["lop", "lop deduction", "unpaid leave deduction", "loss of pay", "unpaid leave cut", "lop amount", "leave deduction"],
    "arrears": ["arrears", "arrear", "back pay", "salary arrears"],
    "one_time.bonus": ["bonus", "one time bonus", "one-time bonus"],
    "earned.basic": ["basic", "basic earned", "earned basic", "basic pay"],
    "earned.hra": ["hra", "hra earned", "earned hra"],
    "earned.special_allowance": ["special allowance", "special", "special allowance earned"],
    "total_deductions": ["total deductions", "deductions"],
    "employer_cost": ["ctc", "cost to company", "employer cost"],
    "unpaid_days": ["unpaid days", "lop days"],
    "fixed_gross": ["monthly salary", "fixed gross", "contracted gross"],
}
_ALIAS = {re.sub(r"[^a-z0-9]+", " ", a.lower()).strip(): key for key, names in ITEM_ALIASES.items() for a in names + [key, key.replace("_", " ")]}
META_COLUMNS = {
    "case": ["case", "scenario"],
    "month": ["month", "period", "pay month", "payroll month"],
    "employee_id": ["employee id", "emp id", "employee code", "emp code", "id", "code"],
    "employee_name": ["employee name", "employee", "name", "emp name"],
    "item": ["item", "component", "field", "line", "amount type", "what"],
    "expected": ["expected", "expected amount", "amount", "value", "expected value"],
    "reason": ["reason", "why", "explanation", "note", "notes"],
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def item_key(name: str) -> str | None:
    return _ALIAS.get(_norm(name))


def _parse_month(value: str, fy_start: int) -> str:
    text = str(value).strip()
    try:
        return str(Period.parse(text))
    except ValueError:
        pass
    words = _norm(text).split()
    month = next((MONTHS[w] for w in words if w in MONTHS), None)
    if month is None:
        raise ValueError(f"cannot read month {value!r}")
    year = next((int(w) for w in words if re.fullmatch(r"20\d{2}", w)), None)
    if year is None:
        year = fy_start if month >= 4 else fy_start + 1
    return f"{year:04d}-{month:02d}"


def _amount(value: str) -> Decimal | None:
    text = str(value).replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    if text in ("", "-"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@dataclass
class Expectation:
    month: str
    employee_id: str
    employee_name: str
    item: str
    item_key: str | None
    expected: Decimal
    reason: str = ""


def read_expected(path: Path, fy_start: int, case: str | None = None) -> list[Expectation]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = {}
        for col in reader.fieldnames or []:
            n = _norm(col)
            for meta, names in META_COLUMNS.items():
                if n in names and meta not in columns:
                    columns[meta] = col
        rows = list(reader)
    out = []
    long_format = "item" in columns and "expected" in columns
    for row in rows:
        if case and columns.get("case") and row[columns["case"]].strip() and row[columns["case"]].strip().upper() != case.upper():
            continue
        month = _parse_month(row[columns["month"]], fy_start)
        emp_id = row.get(columns.get("employee_id", ""), "").strip()
        emp_name = row.get(columns.get("employee_name", ""), "").strip()
        if long_format:
            value = _amount(row[columns["expected"]])
            if value is None:
                continue
            out.append(Expectation(month, emp_id, emp_name, row[columns["item"]], item_key(row[columns["item"]]), value,
                                   row.get(columns.get("reason", ""), "")))
        else:
            used = set(columns.values())
            for col, raw in row.items():
                if col in used:
                    continue
                value = _amount(raw)
                if value is None:
                    continue
                out.append(Expectation(month, emp_id, emp_name, col, item_key(col), value))
    return out


def _find_employee(result: dict, exp: Expectation) -> dict | None:
    emps = result["employees"]
    if exp.employee_id:
        for e in emps:
            if e["code"].lower() == exp.employee_id.lower():
                return e
    if exp.employee_name:
        name = _norm(exp.employee_name)
        for e in emps:
            if _norm(e["name"]) == name:
                return e
        first = [e for e in emps if _norm(e["name"]).split()[0] == name.split()[0]]
        if len(first) == 1:
            return first[0]
    return None


def _reason(emp: dict, key: str) -> str:
    def walk(lines):
        for line in lines:
            if line["code"] == key:
                return line
            found = walk(line.get("children", []))
            if found:
                return found
        return None
    line = walk(emp["lines"])
    return (line["formula"] or line["reason"]) if line else ""


class _Rollback(Exception):
    pass


def build_choices(base: dict, overrides: list[str], case: str | None = None) -> dict:
    """A copy of ``base`` (another case's choices) with ``setting.field=value`` overrides applied.

    Values are read as JSON when they parse (``26``, ``null``, ``true``) and as text otherwise (``full``).
    The settings are validated here, before anything runs, so a typo or an unsupported value
    stops the case with a clear message instead of quietly using a default.
    """
    from payroll_engine.policies import COMPANY_DEFAULTS, validate_payload

    choices = json.loads(json.dumps(base))
    choices.pop("_comment", None)
    settings_ = choices.setdefault("settings", {})
    for item in overrides:
        target, sep, raw = item.partition("=")
        key, dot, field = target.strip().partition(".")
        if not sep or not dot or not field:
            raise ValueError(f"--set {item!r}: write it as setting.field=value, e.g. lop.divisor=26")
        if key not in COMPANY_DEFAULTS:
            raise ValueError(f"--set {item!r}: unknown setting '{key}' (known: {', '.join(sorted(COMPANY_DEFAULTS))})")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw.strip()
        settings_.setdefault(key, dict(COMPANY_DEFAULTS[key]))[field] = value
    problems = [f"{key}: {e}" for key, payload in settings_.items() for e in validate_payload(key, payload)]
    if problems:
        raise ValueError("These company choices are not supported: " + "; ".join(problems))
    if case:
        choices["case"] = case
    return choices


def run_case(folder: Path, expected_path: Path | None = None, data_path: Path | None = None, log=print,
             choices: dict | None = None) -> dict:
    """Run one case in a transaction that is rolled back, so the database is left untouched.

    ``choices`` replaces the folder's choices.json (used for a case given as a CSV only)."""
    folder = Path(folder)
    if choices is None:
        choices = json.loads((folder / "choices.json").read_text())
    expected_path = Path(expected_path or folder / "expected.csv")
    repo = Path(settings.REPO_DIR)
    data_path = Path(data_path or repo / choices.get("data", "data/brickline/brickline_hr_data.xlsx"))
    fy_start = int(choices.get("effective_from", "2025-04-01")[:4])
    case_name = choices.get("case") or folder.name.replace("case_", "")
    expectations = read_expected(expected_path, fy_start, case=None)
    months = sorted(set(choices.get("paid_months", [])) | {e.month for e in expectations})
    if not months:
        raise ValueError("No months to run")
    first = Period.parse(months[0]).fy_first
    all_months, p = [], first
    while p <= Period.parse(months[-1]):
        all_months.append(str(p))
        p = p.next()

    report: dict = {}
    with tempfile.TemporaryDirectory() as media, override_settings(AI_PROVIDER="off", MEDIA_ROOT=media):
        try:
            with transaction.atomic():
                setup.ensure_users(create_demo=False)
                setup.load_statutory_rules()
                company = setup.ensure_company(f"answer-sheet-{_norm(case_name).replace(' ', '-')}", f"Answer sheet case {case_name}", "KA")
                setup.set_company_settings(company, choices.get("settings", {}), choices.get("effective_from", "2025-04-01"))
                setup.import_spreadsheet(company, data_path, log=lambda *_: None)
                results = {}
                for month in all_months:
                    instructions = choices.get("instructions", {}).get(month, [])
                    run = setup.run_month(company, month, instructions, finalize=True, log=lambda *_: None)
                    results[month] = run.result
                    log(f"  {Period.parse(month).label}: calculated and approved (result {run.result_hash[:12]})")
                rows = []
                for exp in expectations:
                    result = results.get(exp.month)
                    emp = _find_employee(result, exp) if result else None
                    if exp.item_key is None:
                        status, actual = "unknown item", None
                    elif emp is None:
                        status, actual = "employee not found", None
                    else:
                        actual = d(emp["amounts"].get(exp.item_key, "0"))
                        status = "match" if actual == exp.expected else "MISMATCH"
                    rows.append({
                        "month": exp.month, "employee": (emp or {}).get("name") or exp.employee_name or exp.employee_id,
                        "item": exp.item, "expected": to_str(exp.expected), "actual": to_str(actual) if actual is not None else "",
                        "status": status, "our_reason": _reason(emp, exp.item_key) if emp and exp.item_key else "",
                        "sheet_reason": exp.reason,
                    })
                report = {
                    "case": case_name,
                    "total": len(rows),
                    "matched": sum(1 for r in rows if r["status"] == "match"),
                    "rows": rows,
                }
                raise _Rollback
        except _Rollback:
            pass
    return report


def format_report(report: dict) -> str:
    lines = [f"Case {report['case']}: {report['matched']} of {report['total']} numbers match"]
    width = max((len(r["employee"]) for r in report["rows"]), default=8)
    for r in report["rows"]:
        mark = "✓" if r["status"] == "match" else "✗"
        line = f"  {mark} {r['month']}  {r['employee']:<{width}}  {r['item']:<16} expected {r['expected']:>10}  got {r['actual'] or '-':>10}"
        if r["status"] != "match":
            line += f"  [{r['status']}]  {r['our_reason']}"
        lines.append(line)
    return "\n".join(lines)
