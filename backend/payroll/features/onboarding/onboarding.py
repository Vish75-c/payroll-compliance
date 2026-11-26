"""Bringing a new company onto the system: its details, its pay settings, its HR data, pay and TDS from
earlier in the year (opening balances), and its first month.

Where the company is in this is worked out from what exists (settings, reads, reviewed data, months),
never stored, so the steps can be left and picked up again, or done in another order.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.text import slugify

from payroll_engine.money import inr, to_str
from payroll_engine.periods import Period
from payroll_engine.states import STATES, state_code

from ...shared.permissions import RuleViolation, require_role
from ..companies.models import Company
from ..hr_data.canonical import normalize_header
from ..hr_data.models import DataSnapshot, IngestionJob
from ..payroll_runs.models import PayrollRun
from ..pay_groups import service as pay_groups
from ..rules.models import PolicyVersion
from .models import OpeningBalance

PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
DETAILS = ("name", "state", "city", "pan", "tan", "pf_establishment_code", "esi_employer_code", "pay_day")


def _clean_details(data: dict, company: Company | None = None) -> dict:
    out = {}
    for key in DETAILS:
        if key not in data:
            continue
        value = data[key]
        value = value.strip() if isinstance(value, str) else value
        if key == "name":
            if not value:
                raise RuleViolation("Give the company's name.")
            out["name"] = value[:200]
        elif key == "state":
            code = state_code(value)
            if not code:
                raise RuleViolation(f"'{value}' is not an Indian state or union territory. Use a name such as Karnataka or a code such as KA.")
            out["state"] = code
        elif key in ("pan", "tan"):
            value = (value or "").upper()
            if value and not (PAN if key == "pan" else TAN).match(value):
                example = "ABCDE1234F" if key == "pan" else "MUMN12345A"
                raise RuleViolation(f"The {key.upper()} '{value}' does not look right: it should look like {example}.")
            out[key] = value
        elif key == "pay_day":
            if value in (None, ""):
                out["pay_day"] = None
            else:
                try:
                    day = int(value)
                except (TypeError, ValueError):
                    day = 0
                if not 1 <= day <= 31:
                    raise RuleViolation("The pay day must be a day of the month, from 1 to 31.")
                out["pay_day"] = day
        else:
            out[key] = (value or "")[:100 if key == "city" else 30]
    return out


@transaction.atomic
def create_company(user, data: dict) -> Company:
    require_role(user, "maker", "add a company")
    details = _clean_details(data)
    if "name" not in details or "state" not in details:
        raise RuleViolation("A company needs a name and the state of its main office.")
    if Company.objects.filter(name__iexact=details["name"]).exists():
        raise RuleViolation(f"A company called {details['name']} already exists.", code="exists")
    base = slugify(details["name"])[:40] or "company"
    code, n = base, 2
    while Company.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    company = Company.objects.create(code=code, **details)
    pay_groups.ensure_default(company)  # "Everyone else": the calendar month until the pay settings say otherwise
    return company


def update_company(company: Company, user, data: dict) -> Company:
    require_role(user, "maker", "change the company's details")
    details = _clean_details(data, company)
    if "name" in details and Company.objects.filter(name__iexact=details["name"]).exclude(pk=company.pk).exists():
        raise RuleViolation(f"A company called {details['name']} already exists.", code="exists")
    for key, value in details.items():
        setattr(company, key, value)
    if details:
        company.save(update_fields=list(details))
    return company


# --------------------------------------------------------------------------- opening balances

COLUMNS = {
    "code": ("employee id", "employee code", "badge id", "emp id", "employee"),
    "gross": ("gross paid this year", "gross paid", "gross", "taxable gross", "salary paid"),
    "tds": ("tds this year", "tds deducted", "tds", "tax deducted"),
}


def template_csv() -> str:
    return "Employee ID,Gross paid this year,TDS this year\n"


def _rows_from_file(name: str, content: bytes) -> list[dict]:
    if name.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        rows = [["" if c is None else str(c) for c in r] for r in sheet.iter_rows(values_only=True)]
    else:
        text = content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise RuleViolation("The file is empty.")
    headers = [normalize_header(h) for h in rows[0]]
    where = {}
    for field, names in COLUMNS.items():
        idx = next((i for i, h in enumerate(headers) if h in {normalize_header(n) for n in names}), None)
        if idx is None:
            raise RuleViolation(f"The file needs the columns Employee ID, Gross paid this year and TDS this year "
                                f"(found: {', '.join(rows[0])}).")
        where[field] = idx
    return [{f: (r[i] if i < len(r) else "") for f, i in where.items()} for r in rows[1:]]


def _amount(value, what: str, line: int) -> Decimal:
    text = str(value or "").replace(",", "").replace("₹", "").strip() or "0"
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise RuleViolation(f"Row {line}: {what} '{value}' is not a number.") from exc
    if amount < 0:
        raise RuleViolation(f"Row {line}: {what} cannot be negative.")
    return amount


@transaction.atomic
def save_opening_balances(company: Company, user, first_period: str, rows: list[dict] | None = None,
                          file_name: str = "", content: bytes = b"") -> dict:
    """Pay and TDS of each employee from 1 April up to the month before ``first_period``. An upload replaces
    the earlier one for that year. Refused once a month of that year is submitted or final: its TDS used them."""
    require_role(user, "maker", "enter opening balances")
    try:
        first = Period.parse(first_period)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    if first.month == 4:
        raise RuleViolation("The first month is April, the start of the financial year: there is nothing paid earlier in the year.")
    fy_start = first.fy_first.start
    used = PayrollRun.objects.filter(company=company, run_type=PayrollRun.TYPE_REGULAR, period__gte=str(first.fy_first),
                                     period__lte=str(first.fy_last),
                                     status__in=[PayrollRun.AWAITING_APPROVAL, PayrollRun.APPROVED, PayrollRun.FINALIZED]).first()
    if used:
        raise RuleViolation(f"{used.period_obj.label} is already {used.get_status_display().lower()} and its TDS used the opening "
                            "balances, so they can no longer change.")
    if content:
        rows = _rows_from_file(file_name, content)
    rows = rows or []
    snapshot = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
    if snapshot is None:
        raise RuleViolation("Review the HR data first: opening balances are checked against the employees in it.")
    known = {e["code"]: e for e in snapshot.data.get("employees", [])}
    seen, clean, problems = set(), [], []
    for line, row in enumerate(rows, start=2):
        code = str(row.get("code") or row.get("employee_code") or "").strip()
        if not code:
            continue
        if code not in known:
            problems.append(f"row {line}: {code} is not in the reviewed HR data")
            continue
        if code in seen:
            problems.append(f"row {line}: {code} appears twice")
            continue
        seen.add(code)
        gross, tds = _amount(row.get("gross"), "Gross paid", line), _amount(row.get("tds"), "TDS", line)
        if tds > gross:
            problems.append(f"row {line}: {code}'s TDS ({inr(tds)}) is more than the pay ({inr(gross)})")
            continue
        clean.append((code, gross, tds))
    if problems:
        raise RuleViolation("Nothing was saved. " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else "") + ".")
    if not clean:
        raise RuleViolation("No rows with an Employee ID were found.")
    OpeningBalance.objects.filter(company=company, fy_start=fy_start).delete()
    OpeningBalance.objects.bulk_create([OpeningBalance(company=company, fy_start=fy_start, employee_code=c, gross=g, tds=t, entered_by=user)
                                        for c, g, t in clean])
    return opening_summary(company, first)


def opening_summary(company: Company, first: Period) -> dict:
    fy_start = first.fy_first.start
    rows = list(OpeningBalance.objects.filter(company=company, fy_start=fy_start))
    snapshot = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
    people = {e["code"]: e for e in (snapshot.data.get("employees", []) if snapshot else [])}
    # People who joined before the first month should have pay earlier in the year; say who is missing.
    entered = {r.employee_code for r in rows}
    missing = sorted(code for code, e in people.items()
                     if code not in entered
                     and (e.get("date_of_joining") or "") < first.start.isoformat()   # no joining date counts as before
                     and not (e.get("date_of_exit") and e["date_of_exit"] < fy_start.isoformat()))
    return {
        "financial_year": first.fy_label, "fy_start": fy_start.isoformat(),
        "covers": f"April {fy_start.year} to {first.prev().label}" if first.month != 4 else "",
        "count": len(rows),
        "gross": to_str(sum((r.gross for r in rows), Decimal(0))),
        "tds": to_str(sum((r.tds for r in rows), Decimal(0))),
        "rows": [{"employee_code": r.employee_code, "name": people.get(r.employee_code, {}).get("name", ""),
                  "gross": to_str(r.gross), "tds": to_str(r.tds)} for r in rows],
        "missing": [{"employee_code": c, "name": people[c].get("name", "")} for c in missing],
    }


# --------------------------------------------------------------------------- where the company is

def state(company: Company) -> dict:
    settings_count = PolicyVersion.objects.filter(company=company).count()
    jobs = IngestionJob.objects.filter(company=company).order_by("-id")
    approved = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
    pending = DataSnapshot.objects.filter(company=company, status="pending_review").order_by("-id").first()
    runs = PayrollRun.objects.filter(company=company, run_type=PayrollRun.TYPE_REGULAR).exclude(status=PayrollRun.CANCELLED).order_by("period")
    first_run = runs.first()
    openings = OpeningBalance.objects.filter(company=company).order_by("-fy_start")
    latest = jobs.first()
    return {
        "company": bool(company.name and company.state),
        "details_complete": bool(company.pf_establishment_code),
        "settings": settings_count > 0,
        "hr_data": {"done": jobs.exists(), "job_id": latest.pk if latest else None, "status": latest.status if latest else ""},
        "data_checked": {"done": approved is not None, "snapshot_id": (approved or pending).pk if (approved or pending) else None,
                         "employees": len(approved.data.get("employees", [])) if approved else 0},
        "opening_balances": {"count": openings.count(), "fy_start": openings.first().fy_start.isoformat() if openings.exists() else ""},
        "first_month": {"done": first_run is not None, "run_id": first_run.pk if first_run else None,
                        "period": first_run.period if first_run else ""},
    }


def states_list() -> list[dict]:
    return [{"code": c, "name": n} for c, n in sorted(STATES.items(), key=lambda kv: kv[1])]


def fy_start_of(period: str) -> date:
    return Period.parse(period).fy_first.start


# States whose professional tax the engine cannot work out yet: charged twice a year on half-yearly pay, or fixed on
# annual income. Shown at onboarding so nobody assumes PT is being deducted there.
PT_NOT_SUPPORTED = {
    "TN": "charges professional tax twice a year on half-yearly pay", "KL": "charges professional tax twice a year on half-yearly pay",
    "PY": "charges professional tax twice a year on half-yearly pay", "MP": "fixes professional tax on annual income",
    "BR": "fixes professional tax on annual income", "JH": "fixes professional tax on annual income",
    "PB": "fixes professional tax on annual income",
}


def professional_tax_for(state: str, on: date | None = None) -> dict:
    """Which professional tax applies to a company in ``state`` (the state picked at onboarding), as of ``on``."""
    from django.utils import timezone

    code = state_code(state)
    name = STATES.get(code, state or "")
    if not code:
        return {"state": "", "status": "no_state", "message": "Pick the company's state to see which professional tax applies."}
    on = on or timezone.localdate()
    versions = PolicyVersion.objects.filter(company__isnull=True, key="professional_tax", jurisdiction=f"IN-{code}").order_by("effective_from", "id")
    current = [v for v in versions if v.effective_from <= on] or list(versions)
    version = current[-1] if current else None
    out = {"state": code, "state_name": name}
    if version is None:
        why = PT_NOT_SUPPORTED.get(code)
        out.update(status="not_supported" if why else "missing",
                   message=(f"{name} {why}, which the payroll does not work out yet: PT is ₹0 there." if why else
                            f"No professional tax rule is stored for {name}. If {name} charges it, add its slabs on Rules & settings "
                            "before the first month; until then PT is ₹0."))
        return out
    payload = version.payload
    out.update(effective_from=version.effective_from.isoformat(), title=version.title, note=version.note, version_id=version.pk)
    if payload.get("levied") is False:
        out.update(status="not_charged", message=f"{name} does not charge professional tax, so PT is ₹0.")
        return out
    out.update(status="slabs", slabs=payload.get("slabs", []), due_day=payload.get("due_day_next_month"),
               message=f"{name}'s professional tax slabs from {version.effective_from:%d %b %Y} apply automatically to employees working in "
                       f"{name}, and to anyone with no work location.")
    if not payload.get("due_day_next_month"):
        out["warning"] = (f"The {name} rule has no payment due day. Add 'due_day_next_month' on Rules & settings before the first month is "
                          "finalised, or finalising will stop.")
    if "secondary sources" in (version.note or ""):
        out["verify"] = "These slabs come from payroll and tax guides, not the state's own notification. Check them before the first month."
    return out
