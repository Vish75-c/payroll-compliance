"""A month's one-time inputs from a sheet: bonuses, reimbursements, recoveries, voluntary PF, unpaid days, overtime.

Any HR tool's "salary inputs for the month" file works the same way: each column gets a *treatment* (taxable
earning, reimbursement, deduction, voluntary PF...) and the column's own name becomes the payslip label, so a
"Diwali Bonus" or a "Canteen Recovery" needs no code. A person reviews the treatments and the amounts, then
confirms: the upload is one proposed change of the month, and nothing reaches pay before it is confirmed.

Columns of a kind not supported yet (perquisites, a fixed TDS, leaving items) are never dropped silently: if they
hold amounts, the change cannot be confirmed until a person sets them to "not used" on purpose.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from payroll_engine.money import inr, to_str
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, require_role

from ..hr_data.canonical import TARGETS_BY_KEY, normalize_header, part_code
from ..hr_data.models import FieldMapping
from ..hr_data.spreadsheet import read_spreadsheet
from ..hr_data.values import ValueError_, is_blank, parse_amount, parse_number, parse_period
from .cycle import window_for
from .events import record
from .models import ChangeRequest, PayrollRun

SOURCE = "monthly_inputs"
MAPPING_NAME = "Monthly inputs"

TREATMENTS: dict[str, str] = {
    "employee_code": "Employee ID",
    "month": "Payroll month",
    "taxable_earning": "Taxable earning (bonus, incentive, allowance…)",
    "overtime": "Overtime pay",
    "reimbursement": "Reimbursement (not taxable)",
    "advance_paid": "Advance paid (not income; recovered later)",
    "deduction": "Deduction (recovery, loan, advance…)",
    "vpf": "Voluntary PF (VPF)",
    "unpaid_days": "Unpaid days for the month (replaces the HR data's figure)",
    "overtime_hours": "Overtime hours (× rate, when there is no overtime pay)",
    "overtime_rate": "Overtime rate per hour",
    "perquisite": "Perquisite (taxed, not paid in cash)",
    "tds_override": "TDS for the month (instead of the one worked out)",
    "notice_pay": "Notice pay (on leaving; taxable)",
    "notice_recovery": "Notice shortfall recovery (on leaving; deduction)",
    "leave_encashment_exit": "Leave encashment on leaving (exempt up to the limit)",
    "gratuity": "Gratuity (exempt up to the limit)",
    "severance": "Severance or retrenchment pay (on leaving; taxable)",
    "leaving_item": "A payment on leaving (choose which kind)",
    "salary_rate": "Monthly salary rate (belongs in the salary file)",
    "ignore": "Not used",
}
EXIT_ONLY = {"notice_pay", "notice_recovery", "leave_encashment_exit", "gratuity", "severance"}
AMOUNTS = {"taxable_earning", "overtime", "reimbursement", "advance_paid", "deduction", "vpf", "perquisite", "tds_override"} | EXIT_ONLY
NOT_SUPPORTED = {
    "leaving_item": "it is not said which kind of payment on leaving it is (notice pay, notice recovery, leave encashment, "
                    "gratuity or severance)",
    "salary_rate": "a monthly salary rate belongs in the salary file, not in a month's one-time inputs",
}

# The first rule that matches a column's name suggests its treatment; a person confirms every column.
RULES: list[tuple[str, str]] = [
    (r"\b(remarks?|notes?|comments?|description|reason)\b", "ignore"),
    (r"^(effective from|payroll month|month|period|pay period|salary month|for the month|month of payment|payout month)$", "month"),
    (r"\b(ot|overtime) hours?\b", "overtime_hours"),
    (r"\b(ot|overtime) rate\b", "overtime_rate"),
    (r"\b(ot|overtime) days?\b", "ignore"),
    (r"\bovertime\b|^ot$|^ot amount$", "overtime"),
    (r"\b(direct|fixed|manual) tds\b|\btds override\b", "tds_override"),
    (r"\btds\b|\bsurcharge\b|\bcess\b", "ignore"),
    (r"\bvpf\b|\bvoluntary (pf|provident)", "vpf"),
    (r"\bperq|\bperquisite|\bassets in residence\b|\besop\b", "perquisite"),
    (r"\bexempt\b|^fbp n$|^fbp adj$", "ignore"),
    (r"\bencashment days\b|\bshortfall days\b|\bexcess days\b", "ignore"),
    (r"\bleave encashment (on|at) (exit|leaving|separation)\b|\b(final|exit) leave encashment\b", "leave_encashment_exit"),
    (r"\bleave encashment\b", "taxable_earning"),
    (r"\bgratuity (deduction|recovery)\b", "deduction"),
    (r"\bgratuity allowance\b", "taxable_earning"),
    (r"\bgratuity\b", "gratuity"),
    (r"\bnotice (period )?(recovery|shortfall)\b|\bnotice shortfall\b", "notice_recovery"),
    (r"\bnotice (period )?pay\b|^notice period$|\bpay in lieu of notice\b", "notice_pay"),
    (r"\bseverance\b|\bretrenchment\b", "severance"),
    (r"\bfull and final\b|\bf f settlement\b", "leaving_item"),
    (r"^full (basic|da|hra|conveyance|medical allowance|special allowance)$", "salary_rate"),
    (r"\badvance (payout|paid|given|disbursed)\b", "advance_paid"),
    (r"\brecovery\b|\bdeduction\b|\bloan\b|\badvance\b|\bemi\b|\bcanteen\b|\bfine\b|\bpenalty\b", "deduction"),
    (r"reimburs|\bbills?\b|\bclaim(ed)?\b", "reimbursement"),
    (r"\blop\b|\bloss of pay\b|\bunpaid days\b|\blwp\b", "unpaid_days"),
    (r"bonus|incentive|commi(ss|s)ion|award|allowance|earning|\bpay\b|arrear|stipend|honorarium|assistance|ex gratia|gift|"
     r"\bpayout\b|\bprize\b", "taxable_earning"),
]
ID_NAMES = set(TARGETS_BY_KEY["employee.code"].synonyms) | {"employee number", "emp number"}


def suggest(headers: list[str], rows: list[dict]) -> dict[str, dict]:
    out = {}
    id_taken = False
    for h in headers:
        norm = normalize_header(h)
        if not id_taken and norm in ID_NAMES:
            out[h] = {"treatment": "employee_code", "sure": True}
            id_taken = True
            continue
        found = next((t for pattern, t in RULES if re.search(pattern, norm)), None)
        if found:
            out[h] = {"treatment": found, "sure": True}
        elif any(_amount(r.get(h)) for r in rows):
            out[h] = {"treatment": "taxable_earning", "sure": False}  # has amounts but an unknown name: a person checks it
        else:
            out[h] = {"treatment": "ignore", "sure": True}
    return out


def _amount(raw) -> Decimal | None:
    if is_blank(raw):
        return None
    try:
        value = parse_amount(str(raw))
    except ValueError_:
        return None
    return value if value != 0 else None


def label_of(header: str) -> str:
    return " ".join(w.capitalize() if not w.isupper() or len(w) > 4 else w for w in str(header).split()) if header else "Item"


def _fingerprint(headers: list[str]) -> str:
    return hashlib.sha256("|".join(sorted(normalize_header(h) for h in headers)).encode()).hexdigest()


# --------------------------------------------------------------------------- upload, review, confirm

@transaction.atomic
def upload(run: PayrollRun, user, filename: str, content: bytes) -> ChangeRequest:
    require_role(user, "maker", "upload the month's inputs")
    from .changes import _check_month_open

    _check_month_open(run)
    if run.run_type != PayrollRun.TYPE_REGULAR:
        raise RuleViolation("A draft re-run is a what-if; upload the month's inputs on the month itself.")
    digest = hashlib.sha256(content).hexdigest()
    same = ChangeRequest.objects.filter(company=run.company, period=run.period, proposal__source=SOURCE,
                                        proposal__sha256=digest).exclude(status=ChangeRequest.REJECTED).first()
    if same:
        raise RuleViolation(f"This file is already in {run.period_obj.label} (change #{same.pk}); adding it again would pay "
                            "every amount twice.", code="same_file")
    try:
        tables = read_spreadsheet(filename, content)
    except Exception as exc:  # noqa: BLE001 - an unreadable file is reported to the user
        raise RuleViolation(f"Could not read {filename}: {exc}") from exc
    if not tables:
        raise RuleViolation(f"{filename}: no table with column headings was found.")
    table = max(tables, key=lambda t: len(t["rows"]))
    saved = FieldMapping.objects.filter(company=run.company, source_system=MAPPING_NAME, fingerprint=_fingerprint(table["headers"]),
                                        confirmed_at__isnull=False).order_by("-confirmed_at").first()
    suggestion = suggest(table["headers"], table["rows"])
    columns = {h: (saved.mapping.get(h) if saved else None) or suggestion[h]["treatment"] for h in table["headers"]}
    change = ChangeRequest(company=run.company, period=run.period, run=run, created_by=user, status=ChangeRequest.PROPOSED, text="")
    change.proposal = {"source": SOURCE, "file": filename, "sha256": digest, "headers": table["headers"], "rows": table["rows"],
                       "notes_skipped": table["read_info"].get("notes_skipped", []), "suggestion": suggestion,
                       "reused_mapping": bool(saved), "columns": columns}
    _work_out(change, run)
    change.save()
    record(run, user, "inputs_uploaded", f"Uploaded {filename}: {change.text}" + (" Column treatments reused from an earlier upload." if saved else ""),
           change_id=change.pk)
    return change


@transaction.atomic
def set_columns(change: ChangeRequest, user, columns: dict) -> ChangeRequest:
    require_role(user, "maker", "review the month's inputs")
    change = ChangeRequest.objects.select_for_update().get(pk=change.pk)
    if change.proposal.get("source") != SOURCE or change.status != ChangeRequest.PROPOSED:
        raise RuleViolation("Only an uploaded sheet of the month's inputs that is not confirmed yet can be changed.")
    unknown = [t for t in columns.values() if t not in TREATMENTS]
    if unknown:
        raise RuleViolation(f"Unknown treatment: {', '.join(sorted(set(unknown)))}.")
    proposal = dict(change.proposal)
    proposal["columns"] = {h: columns.get(h, proposal["columns"].get(h, "ignore")) for h in proposal["headers"]}
    change.proposal = proposal
    _work_out(change, change.run)
    change.save()
    return change


def remember(change: ChangeRequest, user):
    """On confirmation: keep the column treatments for the next file with the same columns."""
    p = change.proposal
    FieldMapping.objects.create(company=change.company, source_system=MAPPING_NAME, table_name=MAPPING_NAME,
                                fingerprint=_fingerprint(p["headers"]), headers=p["headers"], mapping=p["columns"], value_maps={},
                                options={}, confirmed_by=user, confirmed_at=timezone.now())


# --------------------------------------------------------------------------- the items

def _work_out(change: ChangeRequest, run: PayrollRun):
    """Turn the rows and the column treatments into the change's items, problems and notes."""
    p = change.proposal
    columns, rows = p["columns"], p["rows"]
    period = run.period_obj
    by_treatment: dict[str, list[str]] = {}
    for h, t in columns.items():
        by_treatment.setdefault(t, []).append(h)
    items, problems, notes, totals = [], [], [], {}
    ids = by_treatment.get("employee_code", [])
    if len(ids) != 1:
        problems.append("Choose exactly one column as the Employee ID." if not ids else f"Only one column can be the Employee ID ({', '.join(ids)}).")
    for many in ("month", "overtime_hours", "overtime_rate", "unpaid_days", "tds_override"):
        if len(by_treatment.get(many, [])) > 1:
            problems.append(f"Only one column can be '{TREATMENTS[many]}' ({', '.join(by_treatment[many])}).")
    people = {e["code"]: e for e in run.snapshot.data.get("employees", [])}
    unknown, other_month = set(), set()
    for t, why in NOT_SUPPORTED.items():
        for h in by_treatment.get(t, []):
            count = sum(1 for r in rows if _amount(r.get(h)))
            if count:
                problems.append(f"'{h}' has amounts for {count} {'people' if count > 1 else 'person'}, but {why}. Set the column to "
                                "Not used to go ahead without it (and handle it another way), or wait for that feature.")
    if len(ids) == 1:
        for n, row in enumerate(rows, start=1):
            code = str(row.get(ids[0]) or "").strip()
            if not code:
                continue
            if by_treatment.get("month"):
                raw = row.get(by_treatment["month"][0])
                try:
                    row_month = parse_period(raw) if not is_blank(raw) else period
                except ValueError_:
                    problems.append(f"Row {n}: '{raw}' is not a month.")
                    continue
                if row_month != period:
                    other_month.add(str(row_month))
                    continue
            emp = people.get(code)
            if not emp:
                if any(_amount(row.get(h)) for t in AMOUNTS for h in by_treatment.get(t, [])):
                    unknown.add(code)
                continue
            items.extend(_row_items(row, n, emp, by_treatment, period, run, problems, totals))
    if unknown:
        problems.append(f"{len(unknown)} employee ID{'s are' if len(unknown) > 1 else ' is'} not in {period.label}'s HR data: "
                        + ", ".join(sorted(unknown)[:8]) + ("…" if len(unknown) > 8 else "") + ".")
    if other_month:
        problems.append(f"Some rows are for {', '.join(sorted(other_month))}, not {period.label}. Upload them in their own month.")
    if by_treatment.get("advance_paid"):
        notes.append("An advance paid is not income: it is paid now and recovered later as a deduction.")
    for h in by_treatment.get("taxable_earning", []):
        if not (p.get("suggestion") or {}).get(h, {}).get("sure", True) and columns[h] == p["suggestion"][h]["treatment"]:
            notes.append(f"'{h}' was taken as a taxable earning from its amounts; check that is right.")
    unused = [h for h in by_treatment.get("ignore", []) if any(_amount(r.get(h)) for r in rows)]
    if unused:
        notes.append("Not used, though they have amounts: " + ", ".join(unused) + ".")
    people_count = len({i["employee_code"] for i in items})
    p.update(items=items, problems=problems, notes=notes, totals={h: to_str(v) for h, v in totals.items()})
    change.text = (f"Monthly inputs from {p['file']}: {len(items)} amount{'s' if len(items) != 1 else ''} for {people_count} "
                   f"{'people' if people_count != 1 else 'person'} in {period.label}")


def _row_items(row, n, emp, by_treatment, period: Period, run, problems, totals) -> list[dict]:
    out = []
    who = f"{emp.get('name', emp['code'])} ({emp['code']})"
    base = {"employee_code": emp["code"], "employee_name": emp.get("name", emp["code"]), "from_upload": True}

    def money(h):
        raw = row.get(h)
        if is_blank(raw):
            return None
        try:
            value = parse_amount(str(raw))
        except ValueError_:
            problems.append(f"Row {n}, '{h}': '{raw}' is not an amount.")
            return None
        if value < 0:
            problems.append(f"Row {n}, '{h}': a negative amount ({raw}). Use a deduction column for money taken back.")
            return None
        return value or None

    def add(h, value, item):
        totals[h] = totals.get(h, Decimal(0)) + value
        out.append({**base, **item})

    leaving = str(emp.get("date_of_exit") or "")[:7] == str(period)
    for t in ("taxable_earning", "overtime", "reimbursement", "advance_paid", "deduction", "vpf", "perquisite", "tds_override",
              *sorted(EXIT_ONLY)):
        for h in by_treatment.get(t, []):
            value = money(h)
            if not value:
                continue
            label = label_of(h)
            if t in EXIT_ONLY and not leaving:
                problems.append(f"Row {n}, '{h}': {who} does not leave in {period.label}, so a payment on leaving cannot be made. "
                                "Check their date of leaving in the HR data.")
                continue
            if t == "perquisite":
                add(h, value, {"type": "perquisite", "item": part_code(h) or "perquisite", "label": label, "amount": to_str(value),
                               "summary": f"Perquisite {label.lower()} of {inr(value)} for {who} in {period.label}: taxed, not paid in cash."})
                continue
            if t == "tds_override":
                add(h, value, {"type": "tds_override", "amount": to_str(value),
                               "summary": f"Deduct TDS of {inr(value)} from {who} in {period.label}, instead of the amount worked out."})
                continue
            if t in EXIT_ONLY:
                earning = t != "notice_recovery"
                exemption = {"gratuity": "gratuity", "leave_encashment_exit": "leave_encashment"}.get(t)
                add(h, value, {"type": "one_time_earning" if earning else "one_time_deduction", "item": part_code(h) or t, "label": label,
                               "amount": to_str(value), "taxable": earning, **({"exemption": exemption} if exemption else {}),
                               "summary": f"{'Pay' if earning else 'Recover'} {label.lower()} of {inr(value)} {'to' if earning else 'from'} {who} "
                                          f"on leaving in {period.label}" + (" (exempt from tax up to the legal limit)." if exemption else ".")})
                continue
            if t == "vpf":
                add(h, value, {"type": "vpf", "label": label, "amount": to_str(value),
                               "summary": f"Voluntary PF of {inr(value)} from {who} in {period.label} ({h})."})
                continue
            earning = t != "deduction"
            taxable = t in ("taxable_earning", "overtime")
            add(h, value, {"type": "one_time_earning" if earning else "one_time_deduction", "item": part_code(h) or "other",
                           "label": label, "amount": to_str(value), "taxable": taxable,
                           "summary": f"{'Pay' if earning else 'Deduct'} {label.lower()} of {inr(value)} {'to' if earning else 'from'} {who} "
                                      f"in {period.label}" + (" (not taxable)." if earning and not taxable else ".")})
    # Overtime from hours × rate, only when no overtime pay is given for the person.
    hours_col, rate_col = (by_treatment.get("overtime_hours") or [None])[0], (by_treatment.get("overtime_rate") or [None])[0]
    paid_ot = any(money(h) for h in by_treatment.get("overtime", []))
    if hours_col and rate_col and not paid_ot:
        try:
            hours = parse_number(row.get(hours_col)) if not is_blank(row.get(hours_col)) else Decimal(0)
        except ValueError_:
            problems.append(f"Row {n}, '{hours_col}': '{row.get(hours_col)}' is not a number of hours.")
            hours = Decimal(0)
        rate = money(rate_col) or Decimal(0)
        if hours and rate:
            value = (hours * rate).quantize(Decimal(1))
            add(hours_col, value, {"type": "one_time_earning", "item": "overtime", "label": "Overtime", "amount": to_str(value),
                                   "taxable": True, "summary": f"Pay overtime of {inr(value)} to {who} in {period.label} "
                                                              f"({to_str(hours)} hours × {inr(rate)})."})
    for h in by_treatment.get("unpaid_days", [])[:1]:
        raw = row.get(h)
        if is_blank(raw):
            continue
        try:
            days = parse_number(str(raw))
        except ValueError_:
            problems.append(f"Row {n}, '{h}': '{raw}' is not a number of days.")
            continue
        limit = window_for(run.company, period, emp["code"], run.snapshot.data).attendance.days
        if days < 0 or days > limit:
            problems.append(f"Row {n}: {to_str(days)} unpaid days is not possible in {period.label} ({limit} days).")
            continue
        out.append({**base, "type": "unpaid_days", "unpaid_days": to_str(days),
                    "summary": f"Set {who}'s unpaid days in {period.label} to {to_str(days)} (replaces the figure from the HR data)."})
    return out
