"""Payroll register and one payment file each for PF, ESI, professional tax and TDS.

The files are written from the stored result only, then read back and totalled against
the register, so "the payment files match the register" is checked, not assumed.
No government IDs, no real payments.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from payroll_engine import policies as engine_policies
from payroll_engine.engine import component_label, order_components
from payroll_engine.money import d, to_str
from payroll_engine.periods import Period, parse_date, pay_date

from ..dues import dues


class FilesDoNotMatch(Exception):
    """The files read back do not total to the register. A code or data bug: retrying will not help."""


def _due(versions: list[dict], key: str, period: Period, jurisdiction: str, pay_day: int | None = None) -> str:
    when = dues._due_day(versions, key, period, jurisdiction, pay_day)
    return when.isoformat() if when else ""


def register_columns(result: dict) -> list[tuple[str, str]]:
    comps = order_components({k.split(".", 1)[1] for e in result["employees"] for k in e["amounts"] if k.startswith("earned.")})
    one_time = sorted({k for e in result["employees"] for k in e["amounts"] if k.startswith("one_time.")})
    deductions = sorted({k for e in result["employees"] for k in e["amounts"] if k.startswith("deduction.")})
    cols = [("employee_code", "Employee ID"), ("name", "Name"), ("work_state", "State")]
    if any(e.get("pay_window") for e in result["employees"]):
        cols += [("pay_group", "Pay group"), ("pay_from", "Paid from"), ("pay_to", "Paid to"), ("pay_date", "Pay date")]
    cols += [("days_in_month", "Days in month"), ("unpaid_days", "Unpaid days")]
    cols += [(f"rate.{c}", f"{component_label(c)} (monthly)") for c in comps]
    cols += [("fixed_gross", "Monthly salary")]
    cols += [(f"earned.{c}", f"{component_label(c)} (earned)") for c in comps]
    cols += [("lop_deduction", "Unpaid leave cut"), ("arrears", "Arrears")]
    cols += [(k, k.split(".", 1)[1].replace("_", " ").capitalize()) for k in one_time]
    cols += [("gross", "Gross pay"), ("pf_employee", "PF employee"), ("esi_employee", "ESI employee"),
             ("pt", "Professional tax"), ("tds", "TDS")]
    cols += [(k, k.split(".", 1)[1].replace("_", " ").capitalize()) for k in deductions]
    cols += [("total_deductions", "Total deductions"), ("net_pay", "Net pay"), ("pf_employer", "PF employer"),
             ("eps_employer", "Employer EPS"), ("epf_employer", "Employer EPF"), ("esi_employer", "ESI employer"),
             ("employer_cost", "Cost to company")]
    return cols


def register_rows(result: dict) -> tuple[list[str], list[list[str]]]:
    cols = register_columns(result)
    rows = []
    for e in result["employees"]:
        row = []
        for key, _ in cols:
            if key == "employee_code":
                row.append(e["code"])
            elif key == "name":
                row.append(e["name"])
            elif key == "work_state":
                row.append(e.get("work_state", ""))
            elif key in ("pay_group", "pay_from", "pay_to", "pay_date"):
                row.append(_pay_field(e.get("pay_window") or {}, key))
            else:
                row.append(e["amounts"].get(key, "0"))
        rows.append(row)
    return [label for _, label in cols], rows


def _pay_field(pw: dict, key: str) -> str:
    if key == "pay_group":
        return pw.get("group_name", "")
    if key == "pay_from":
        return pw.get("salary_from", "")
    if key == "pay_to":
        return pw.get("salary_to", "")
    return pay_date(parse_date(pw["salary_to"]), int(pw["pay_day"])).isoformat() if pw.get("pay_day") else ""


def _paid_apart(result: dict) -> list[dict]:
    groups = result.get("totals_by_group") or []
    return groups if len(groups) > 1 else []


def _csv(headers: list[str], rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def build_files(result: dict, input_data: dict) -> dict[str, str]:
    period = Period.parse(result["period"])
    versions = input_data.get("policy_versions", [])
    pf_rule = engine_policies.resolve(versions, "pf", period, "IN")
    pf_due = _due(versions, "pf", period, "IN")
    esi_due = _due(versions, "esi", period, "IN")
    tds_due = _due(versions, "income_tax", period, "IN", (input_data.get("company") or {}).get("pay_day"))
    eps_ceiling = d((pf_rule.payload if pf_rule else {}).get("eps_wage_ceiling", "15000"))

    files = {}
    headers, rows = register_rows(result)
    files["register.csv"] = _csv(headers, rows)

    pf_rows, esi_rows, pt_rows, tds_rows = [], [], [], []
    for e in result["employees"]:
        a = e["amounts"]
        pf_wage = d(a.get("pf_wage"))
        employee_share, employer_total = d(a.get("pf_employee")), d(a.get("pf_employer"))
        line = next(x for x in e["lines"] if x["code"] == "pf_employee")
        pf_rows.append([e["code"], e["name"], to_str(pf_wage), line["inputs"].get("base", "0"),
                        to_str(min(pf_wage, eps_ceiling)) if employer_total else "0",
                        to_str(employee_share), a.get("eps_employer", "0"), a.get("epf_employer", "0"),
                        to_str(employer_total), to_str(employee_share + employer_total), pf_due])
        esi_emp, esi_er = d(a.get("esi_employee")), d(a.get("esi_employer"))
        covered = e.get("facts", {}).get("esi_covered", bool(esi_emp or esi_er))
        esi_rows.append([e["code"], e["name"], "Yes" if covered else "No", a.get("esi_wage", "0"),
                         to_str(esi_emp), to_str(esi_er), to_str(esi_emp + esi_er), esi_due])
        state = e.get("work_state", "")
        pt_rows.append([e["code"], e["name"], state, a.get("pt_basis", "0"), a.get("pt", "0"),
                        _due(versions, "professional_tax", period, f"IN-{state}") if state else ""])
        deducted = dues._deducted_in(e)  # each person's TDS is due after the month they are paid in
        own_due = dues.tds_due_date(versions, period, Period.parse(deducted)).isoformat() if deducted else tds_due
        tds_rows.append([e["code"], e["name"], a.get("gross", "0"), a.get("tds_projected_income", "0"),
                         a.get("tds_taxable_income", "0"), a.get("tds_annual_tax", "0"), a.get("tds_paid_before", "0"),
                         a.get("tds", "0"), own_due])

    files["pf.csv"] = _csv(["Employee ID", "Name", "PF wages", "EPF wages (base used)", "EPS wages", "Employee share",
                            "Employer EPS", "Employer EPF", "Employer share", "Total to pay", "Due date"], pf_rows)
    files["esi.csv"] = _csv(["Employee ID", "Name", "Covered", "ESI wages", "Employee share", "Employer share",
                             "Total to pay", "Due date"], esi_rows)
    files["professional_tax.csv"] = _csv(["Employee ID", "Name", "State", "Pay used for PT", "Professional tax", "Due date"], pt_rows)
    files["tds.csv"] = _csv(["Employee ID", "Name", "Gross pay this month", "Estimated pay for the year", "Taxable income",
                             "Tax for the year", "TDS deducted earlier this year", "TDS this month", "Due date"], tds_rows)
    # Net pay per pay group, for the bank, when there is more than one: each group is paid on its own day.
    for g in _paid_apart(result):
        people = [e for e in result["employees"] if (e.get("pay_window") or {}).get("group", "") == g["group"]]
        files[f"net_pay_{g['group'] or 'all'}.csv"] = _csv(
            ["Employee ID", "Name", "Pay group", "Paid from", "Paid to", "Net pay", "Pay date"],
            [[e["code"], e["name"], g["name"], e["pay_window"]["salary_from"], e["pay_window"]["salary_to"],
              e["amounts"].get("net_pay", "0"), g["pay_date"]] for e in people])
    return files


def _column_total(text: str, column: str) -> Decimal:
    reader = csv.DictReader(io.StringIO(text))
    return sum((d(row[column]) for row in reader), Decimal(0))


def reconcile(files: dict[str, str], result: dict) -> list[dict]:
    """Read the files back and compare their totals with the register and with each other."""
    register = files["register.csv"]
    checks = []
    pairs = [
        ("PF employee share", "pf.csv", "Employee share", "PF employee"),
        ("PF employer share", "pf.csv", "Employer share", "PF employer"),
        ("Employer EPS", "pf.csv", "Employer EPS", "Employer EPS"),
        ("Employer EPF", "pf.csv", "Employer EPF", "Employer EPF"),
        ("ESI employee share", "esi.csv", "Employee share", "ESI employee"),
        ("ESI employer share", "esi.csv", "Employer share", "ESI employer"),
        ("Professional tax", "professional_tax.csv", "Professional tax", "Professional tax"),
        ("TDS", "tds.csv", "TDS this month", "TDS"),
    ]
    for label, name, file_col, reg_col in pairs:
        in_file = _column_total(files[name], file_col)
        in_register = _column_total(register, reg_col)
        checks.append({"check": label, "file": name, "file_total": to_str(in_file), "register_total": to_str(in_register),
                       "matches": in_file == in_register})
    register_rows_count = len(list(csv.DictReader(io.StringIO(register))))
    for name in ("pf.csv", "esi.csv", "professional_tax.csv", "tds.csv"):
        count = len(list(csv.DictReader(io.StringIO(files[name]))))
        checks.append({"check": f"{name}: one row per employee", "file": name, "file_total": str(count),
                       "register_total": str(register_rows_count), "matches": count == register_rows_count == result["employee_count"]})
    # What will be deposited with the government equals the files.
    by_state: dict[str, Decimal] = {}
    for row in csv.DictReader(io.StringIO(files["professional_tax.csv"])):
        by_state[row["State"]] = by_state.get(row["State"], Decimal(0)) + d(row["Professional tax"])
    file_totals = {("tds", ""): _column_total(files["tds.csv"], "TDS this month"),
                   ("pf", ""): _column_total(files["pf.csv"], "Total to pay"),
                   ("esi", ""): _column_total(files["esi.csv"], "Total to pay")}
    file_totals.update({("pt", state): total for state, total in by_state.items()})
    owed: dict[tuple[str, str], Decimal] = {}
    for x in dues.amounts_of(result):  # TDS split by the month it is deducted in is one file
        key = ("tds", "") if x["kind"] == "tds" else (x["kind"], x["jurisdiction"])
        owed[key] = owed.get(key, Decimal(0)) + x["total"]
    for key in sorted(set(file_totals) | set(owed)):
        in_file, due_total = file_totals.get(key, Decimal(0)), owed.get(key, Decimal(0))
        name = {"tds": "tds.csv", "pf": "pf.csv", "esi": "esi.csv", "pt": "professional_tax.csv"}[key[0]]
        checks.append({"check": f"Government due: {dues.LABELS[key[0]]}" + (f" ({key[1]})" if key[1] else ""), "file": name,
                       "file_total": to_str(in_file), "register_total": to_str(due_total), "matches": in_file == due_total})
    for g in _paid_apart(result):
        name = f"net_pay_{g['group'] or 'all'}.csv"
        in_file = _column_total(files[name], "Net pay")
        checks.append({"check": f"Net pay of {g['name']} ({g['people']} people)", "file": name, "file_total": to_str(in_file),
                       "register_total": g["totals"].get("net_pay", "0"), "matches": in_file == d(g["totals"].get("net_pay", "0"))})
    net_register = _column_total(register, "Net pay")
    checks.append({"check": "Net pay in register equals the calculation", "file": "register.csv", "file_total": to_str(net_register),
                   "register_total": result["totals"].get("net_pay", "0"), "matches": net_register == d(result["totals"].get("net_pay"))})
    return checks


def write_files(run) -> dict:
    files = build_files(run.result, run.input)
    checks = reconcile(files, run.result)
    if not all(c["matches"] for c in checks):
        bad = [c["check"] for c in checks if not c["matches"]]
        raise FilesDoNotMatch("Payment files do not match the register: " + ", ".join(bad))
    folder = Path(settings.MEDIA_ROOT) / "runs" / f"{run.company.code}-{run.period}-run{run.pk}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"period": run.period, "run_id": run.pk, "result_hash": run.result_hash, "files": {}, "reconciliation": checks}
    for name, text in files.items():
        data = text.encode("utf-8")
        (folder / name).write_bytes(data)
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "rows": text.count("\n") - 1, "bytes": len(data)}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    manifest["folder"] = str(folder.relative_to(settings.MEDIA_ROOT))
    return manifest
