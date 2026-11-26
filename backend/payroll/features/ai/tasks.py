"""The five jobs AI is allowed to do. Each has a rule-based twin for when no model is configured.

Nothing here works out an amount or picks a rule. Answers are suggestions that a person
confirms, and each one is checked by plain code (for example: every number in an AI
summary must appear in the calculation; every value read from a screen must be on it).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from payroll_engine.diff import numbers_in_diff
from payroll_engine.money import to_str

from ..hr_data.canonical import IGNORE, catalogue, heuristic_mapping, is_valid_target, normalize_header
from ..hr_data.values import MONTHS
from . import client

NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

# =========================================================================== 1. field mapping

MAPPING_SYSTEM = (
    "You help a payroll team connect an HR tool to a payroll system. You are given the column names of one table "
    "read from an HR tool or spreadsheet, a few sample rows, and the list of payroll fields. Personal values in the "
    "sample rows are masked to their shape (letters as A/a, digits as 9); category words such as statuses and leave "
    "types are left as they are. For each column pick the "
    "payroll field it means, or 'ignore'. For a column that holds unpaid-leave days for one specific month, use the "
    "target 'attendance.unpaid_days@YYYY-MM'. A salary table with one amount for the whole monthly pay (salary, wage, "
    "contract wage, gross) maps it to 'comp.monthly_gross'; the company's salary structure splits it into parts. Map to "
    "a part such as 'comp.basic' only a column named after that part. A column holding a name with an ID in brackets, "
    "like 'Aaaaa Aaaa (AA999)', maps to 'employee.name' in every table; the ID is taken from the brackets. Give a low "
    "confidence when unsure. Never invent fields. Do not calculate anything. A person will review "
    "your suggestion before it is used."
)
MAPPING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["columns"],
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["column", "target", "confidence", "reason"],
                "properties": {
                    "column": {"type": "string"},
                    "target": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


# Words that say what kind of value a column holds and are not about a person: kept when masking samples.
_CATEGORY_WORDS = {
    "approved", "rejected", "pending", "requested", "cancelled", "canceled", "active", "inactive", "expired", "draft",
    "terminated", "male", "female", "other", "m", "f", "yes", "no", "y", "n", "true", "false", "monthly", "daily",
    "hourly", "weekly", "full day", "half day", "first half", "second half", "paid", "unpaid", "lop", "lwp",
    "loss of pay", "leave without pay", "full time", "part time", "intern", "contract", "permanent",
}


def mask_value(value) -> str:
    """Keep the shape of a sample value, not the value: 'Priya Sharma (BL001)' -> 'Aaaaa Aaaaaa (AA999)'.
    Category words (statuses, leave types, gender, wage type) stay as they are; they help and identify nobody."""
    text = str(value if value is not None else "").strip()
    norm = normalize_header(text)
    if not text or norm in _CATEGORY_WORDS or re.fullmatch(r"[a-z ]*\bleave\b[a-z ]*", norm):
        return text
    return re.sub(r"[A-Z]", "A", re.sub(r"[a-z]", "a", re.sub(r"\d", "9", text)))


def mask_rows(rows: list[dict]) -> list[dict]:
    return [{k: mask_value(v) for k, v in row.items()} for row in rows]


def suggest_mapping(*, company, source_system: str, table_name: str, headers: list[str], sample_rows: list[dict],
                    dataset_hint: str = "", default_year: int | None = None, force: bool = False):
    # Every name a field is known by, and its note: cutting the list hid "wage" and "contract wage" from the
    # model, which then mapped Horilla's "Contract wage" to basic pay instead of the monthly salary.
    fields = [{"key": t["key"], "label": t["label"], "also_called": t["synonyms"], **({"note": t["help"]} if t["help"] else {})}
              for t in catalogue()]
    prompt = json.dumps({
        "hr_tool": source_system,
        "table": table_name,
        "screen_or_sheet_kind": dataset_hint or "unknown",
        "columns": headers,
        "sample_rows": mask_rows(sample_rows[:3]),
        "payroll_fields": fields + [{"key": "ignore", "label": "Not needed for payroll"}],
        "year_for_month_columns_without_a_year": default_year,
    }, ensure_ascii=False, indent=1)

    def fallback():
        guess = heuristic_mapping(headers, dataset_hint, default_year)
        return {"columns": [{"column": h, **guess[h]} for h in headers]}

    def validate(response):
        checks = []
        seen = {c.get("column") for c in response.get("columns", [])}
        for col in response.get("columns", []):
            if col.get("column") not in headers:
                checks.append({"level": "warning", "message": f"AI named a column that does not exist: {col.get('column')!r}; ignored"})
            elif not is_valid_target(col.get("target", "")):
                checks.append({"level": "warning", "message": f"'{col['column']}': AI suggested an unknown field {col.get('target')!r}; set to 'Not used'"})
        for h in headers:
            if h not in seen:
                checks.append({"level": "info", "message": f"AI gave no suggestion for '{h}'; set to 'Not used'"})
        return checks

    return client.ask(purpose="field_mapping", system=MAPPING_SYSTEM, prompt=prompt, schema=MAPPING_SCHEMA,
                      fallback=fallback, validate=validate, company=company, force=force)


def mapping_from_interaction(interaction, headers: list[str]) -> dict[str, dict]:
    """Clean an AI/rule-based answer into {header: {target, confidence, reason}}; invalid parts become 'ignore'."""
    out = {h: {"target": IGNORE, "confidence": 0.0, "reason": "no suggestion"} for h in headers}
    used = set()
    for col in (interaction.response or {}).get("columns", []):
        header, target = col.get("column"), col.get("target", IGNORE)
        if header not in out or not is_valid_target(target):
            continue
        if target != IGNORE and not target.startswith("attendance.unpaid_days@") and target in used:
            continue
        used.add(target)
        try:
            confidence = float(col.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        out[header] = {"target": target, "confidence": round(max(0.0, min(confidence, 1.0)), 2), "reason": str(col.get("reason", ""))[:300]}
    return out


# =========================================================================== 2. instructions

CHANGE_TYPES = ["one_time_earning", "one_time_deduction", "unpaid_days", "salary_revision"]
ITEMS = ["bonus", "incentive", "commission", "reimbursement", "overtime", "other_earning",
         "advance_recovery", "loan_recovery", "other_deduction"]
INSTRUCTION_SYSTEM = (
    "You turn a payroll instruction written in plain English into proposed changes for one payroll month. "
    "Copy amounts and day counts exactly as they are written (for example '₹10,000' or '10k'); never calculate, "
    "convert or derive an amount. If the instruction does not state an amount, set amount_as_written to null. "
    "'month' is the pay month the change belongs to as YYYY-MM, or null when the instruction means the current month. "
    "Set 'unclear' to null when the instruction is clear; use it only when something the change needs is missing or "
    "could mean more than one thing. Leave 'taxable' null unless the instruction says (the system decides the default). "
    "A person will confirm before anything is used."
)
INSTRUCTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes", "unclear"],
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "employee_as_written", "item", "amount_as_written", "unpaid_days_as_written",
                             "effective_from", "month", "taxable", "explanation"],
                "properties": {
                    "type": {"type": "string", "enum": CHANGE_TYPES},
                    "employee_as_written": {"type": "string"},
                    "item": {"anyOf": [{"type": "string", "enum": ITEMS}, {"type": "null"}]},
                    "amount_as_written": NULLABLE_STR,
                    "unpaid_days_as_written": NULLABLE_STR,
                    "effective_from": NULLABLE_STR,
                    "month": NULLABLE_STR,
                    "taxable": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                    "explanation": {"type": "string"},
                },
            },
        },
        "unclear": NULLABLE_STR,
    },
}


def parse_instruction(*, company, text: str, period: str, employees: list[dict], force: bool = False):
    prompt = json.dumps({
        "instruction": text,
        "current_pay_month": period,
        "employees": [{"code": e["code"], "name": e["name"]} for e in employees],
        "allowed_change_types": {
            "one_time_earning": "an extra payment this month only (bonus, incentive, reimbursement...)",
            "one_time_deduction": "a one-off recovery from pay this month (advance or loan recovery...)",
            "unpaid_days": "set the number of unpaid leave days for the month",
            "salary_revision": "a new monthly salary from a start date (effective_from as YYYY-MM-DD)",
        },
    }, ensure_ascii=False, indent=1)
    return client.ask(purpose="instruction", system=INSTRUCTION_SYSTEM, prompt=prompt, schema=INSTRUCTION_SCHEMA,
                      fallback=lambda: rule_based_instruction(text, period, employees), company=company, force=force)


_AMOUNT_RE = re.compile(r"(?i)(?:₹|rs\.?|inr)\s*[\d,]+(?:\.\d+)?(?:\s*(?:k|lakhs?|lacs?|l|cr|crores?)\b)?|\b\d[\d,]*(?:\.\d+)?\s*(?:k|lakhs?|lacs?|cr|crores?)\b|\b\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\b|\b\d{4,}(?:\.\d+)?\b")
_DAYS_RE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:unpaid\s+|lop\s+|leave\s+)?days?\b")
_DATE_RE = re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s*(\d{4})?\b"
                      r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b")


def amounts_written(text: str) -> list[str]:
    return [m.group(0).strip() for m in _AMOUNT_RE.finditer(text)]


def rule_based_instruction(text: str, period: str, employees: list[dict]) -> dict:
    lower = text.lower()
    year = int(period[:4])
    found_emp = None
    for e in sorted(employees, key=lambda e: -len(e["name"])):
        for candidate in [e["name"], e["code"]] + e["name"].split():
            if len(candidate) >= 3 and re.search(rf"\b{re.escape(candidate.lower())}\b", lower):
                found_emp = candidate
                break
        if found_emp:
            break
    amounts = amounts_written(text)
    days = _DAYS_RE.search(text)
    month = None
    if "next month" in lower:
        y, m = int(period[:4]), int(period[5:7])
        month = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"
    else:
        for word, num in MONTHS.items():
            if re.search(rf"\bfor {word}\b|\bin {word}\b", lower) and len(word) > 3:
                month = f"{year:04d}-{num:02d}"
                break
    effective = None
    dm = _DATE_RE.search(text)
    if dm:
        if dm.group(1):
            day, mon, yr = int(dm.group(1)), MONTHS[dm.group(2).lower()], dm.group(3)
        else:
            day, mon, yr = int(dm.group(5)), MONTHS[dm.group(4).lower()], dm.group(6)
        effective = f"{int(yr) if yr else year:04d}-{mon:02d}-{day:02d}"

    change_type, item = None, None
    if re.search(r"\b(raise|hike|increase|increment|revise|revision)\b", lower) and re.search(r"\b(salary|pay|ctc)\b", lower):
        change_type = "salary_revision"
    elif re.search(r"\b(unpaid|lop|loss of pay|leave without pay)\b", lower) and days:
        change_type = "unpaid_days"
    elif re.search(r"\b(deduct|recover|recovery|advance|loan|fine|penalty)\b", lower):
        change_type = "one_time_deduction"
        item = "loan_recovery" if "loan" in lower else "advance_recovery" if "advance" in lower else "other_deduction"
    elif re.search(r"\b(bonus|incentive|commission|reimburse|reimbursement|overtime|award|pay an extra|extra pay|one[- ]time|payment)\b", lower):
        change_type = "one_time_earning"
        for word, code in (("bonus", "bonus"), ("incentive", "incentive"), ("commission", "commission"),
                           ("reimburse", "reimbursement"), ("overtime", "overtime")):
            if word in lower:
                item = code
                break
        item = item or "other_earning"
    if not change_type or not found_emp:
        missing = []
        if not found_emp:
            missing.append("which employee")
        if not change_type:
            missing.append("what kind of change (bonus, deduction, unpaid days or salary change)")
        return {"changes": [], "unclear": "Could not tell " + " and ".join(missing) + "."}
    change = {
        "type": change_type,
        "employee_as_written": found_emp,
        "item": item,
        "amount_as_written": amounts[0] if amounts and change_type != "unpaid_days" else None,
        "unpaid_days_as_written": days.group(1) if days and change_type == "unpaid_days" else None,
        "effective_from": effective if change_type == "salary_revision" else None,
        "month": month,
        "taxable": False if item == "reimbursement" else None,
        "explanation": "Matched by keywords (rule-based parser, no AI model configured).",
    }
    return {"changes": [change], "unclear": None}


# =========================================================================== 3. change summary

SUMMARY_SYSTEM = (
    "You explain to a finance head, in plain words, what changed in this month's payroll compared with the previous "
    "month. Use only the facts and numbers given; never calculate new numbers (no sums, differences or percentages "
    "that are not in the data). Keep it short: a one-sentence headline and up to six bullet points, most important first."
)
SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "points"],
    "properties": {"headline": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}}},
}


def _amounts_in_text(text: str) -> set[str]:
    out = set()
    for m in re.findall(r"₹\s?([\d,]+(?:\.\d+)?)", text):
        out.add(to_str(Decimal(m.replace(",", ""))))
    for m in re.findall(r"(?<![\w₹.,])(\d{1,3}(?:,\d{2,3})+|\d{3,})(?:\.\d+)?(?![\w,])", text):
        out.add(to_str(Decimal(m.replace(",", ""))))
    return out


def summarize_changes(*, company, diff: dict, before_label: str, after_label: str, force: bool = False):
    compact = {
        "comparing": f"{after_label} against {before_label}",
        "totals": [t for t in diff["totals"] if t["delta"] != "0" and t["code"] in ("gross", "net_pay", "total_deductions", "employer_cost", "arrears", "tds", "pf_employee", "esi_employee", "pt")],
        "employees": [
            {"name": e["name"], "net_before": e["net_before"], "net_after": e["net_after"], "net_change": e["net_delta"],
             "why": [d["text"] for d in e["drivers"]],
             "changed_amounts": [{"item": c["label"], "before": c["before"], "after": c["after"]} for c in e["changes"][:8]]}
            for e in diff["employees"] if e["status"] != "unchanged"
        ],
        "rule_changes": [{"rule": r["title"], "now_from": (r["after"] or {}).get("effective_from")} for r in diff["rule_changes"]],
    }
    prompt = json.dumps(compact, ensure_ascii=False, indent=1)

    def fallback():
        changed = [e for e in diff["employees"] if e["status"] != "unchanged"]
        net = next((t for t in diff["totals"] if t["code"] == "net_pay"), None)
        headline = f"{len(changed)} of {len(diff['employees'])} employees are paid differently in {after_label} than in {before_label}."
        if net and net["delta"] != "0":
            headline += f" Total net pay moves from ₹{_fmt(net['before'])} to ₹{_fmt(net['after'])}."
        points = [f"{e['name']}: " + "; ".join(d["text"] for d in e["drivers"]) + "." for e in changed]
        return {"headline": headline, "points": points[:8]}

    allowed = numbers_in_diff(diff) | {to_str(Decimal(n)) for n in re.findall(r"\b\d+\b", prompt)}

    def validate(response):
        text = response.get("headline", "") + " " + " ".join(response.get("points", []))
        unknown = sorted(n for n in _amounts_in_text(text) if n not in allowed)
        if unknown:
            return [{"level": "warning", "message": "This summary mentions numbers that are not in the calculation: "
                     + ", ".join(unknown) + ". Trust the register, not this text."}]
        return [{"level": "ok", "message": "Every number in this summary appears in the calculation."}]

    return client.ask(purpose="change_summary", system=SUMMARY_SYSTEM, prompt=prompt, schema=SUMMARY_SCHEMA,
                      fallback=fallback, validate=validate, company=company, status="informational", force=force)


def _fmt(value: str) -> str:
    from payroll_engine.money import inr
    return inr(Decimal(value))[1:]


# =========================================================================== 4. page discovery

DISCOVERY_SYSTEM = (
    "You are helping a browser agent find its way around an HR tool, the way a person would. From the list of links "
    "on the page, pick the one that most likely shows (a) the list of employees, (b) each employee's salary or pay "
    "structure (contracts, compensation, salary), and (c) leave records or leave requests. Only use URLs from the list. "
    "Use null when no link fits. A person will confirm your choice."
)
DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["employees", "compensation", "leave"],
    "properties": {
        k: {"anyOf": [{"type": "object", "additionalProperties": False, "required": ["url", "reason"],
                       "properties": {"url": {"type": "string"}, "reason": {"type": "string"}}}, {"type": "null"}]}
        for k in ("employees", "compensation", "leave")
    },
}
_DISCOVERY_WORDS = {
    "employees": ["employee", "employees", "people", "staff", "directory", "team", "members", "workforce"],
    "compensation": ["salary", "salaries", "compensation", "pay structure", "pay", "contract", "contracts", "payroll", "ctc", "wage"],
    "leave": ["leave", "leaves", "time off", "absence", "absences", "holiday request", "leave request", "attendance"],
}


def discover_pages(*, company, system_name: str, page_title: str, links: list[dict], force: bool = False):
    prompt = json.dumps({"hr_tool": system_name, "page_title": page_title, "links": links[:300]}, ensure_ascii=False, indent=1)
    urls = {link["url"] for link in links}

    def fallback():
        out = {}
        for dataset, words in _DISCOVERY_WORDS.items():
            best, best_score = None, 0
            for link in links:
                text = normalize_header(link.get("text", "")) + " " + normalize_header(link["url"])
                score = sum(3 if re.search(rf"\b{re.escape(w)}\b", text) else 0 for w in words)
                if dataset == "employees" and re.search(r"\b(leave|salary|contract|payroll|attendance)\b", text):
                    score -= 4
                if dataset == "compensation" and re.search(r"\b(payslip|run|batch|loan|leave)\b", text):
                    score -= 2
                if score > best_score:
                    best, best_score = link, score
            out[dataset] = {"url": best["url"], "reason": f"link text '{best.get('text', '')}' (keyword match)"} if best else None
        return out

    def validate(response):
        checks = []
        for k in ("employees", "compensation", "leave"):
            v = response.get(k)
            if v and v.get("url") not in urls:
                checks.append({"level": "error", "message": f"Suggested {k} URL is not a link on the page: {v.get('url')}"})
        return checks

    return client.ask(purpose="page_discovery", system=DISCOVERY_SYSTEM, prompt=prompt, schema=DISCOVERY_SCHEMA,
                      fallback=fallback, validate=validate, company=company, force=force)


# --------------------------------------------------------------------------- 4b. one step at a time

NAVIGATION_SYSTEM = (
    "You guide a browser agent through an HR tool, one click at a time, the way a person finds their way around. "
    "You are told what the agent is looking for, the screen it is on, what it has clicked so far, and every link, tab "
    "and button it can click now, each with a number. Answer with the number of the one click most likely to lead to "
    "what it is looking for: a menu item, a section, a tab, or a report. Menus often use short names (for example 'Org' "
    "for employees, 'Time' for leave and attendance, 'Payroll' for salaries), and lists are often inside a tab or under "
    "'Reports'. Only use a number from the list. If nothing on the screen can lead there, answer null. Never choose "
    "something that changes data. A person will check the whole path before it is used."
)
NAVIGATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["click", "reason"],
    "properties": {"click": {"anyOf": [{"type": "integer"}, {"type": "null"}]}, "reason": {"type": "string"}},
}
# Words that suggest a click leads towards a kind of list, with menu names HR tools use for whole sections.
_NAVIGATION_WORDS = {
    "employees": {**{w: 3 for w in _DISCOVERY_WORDS["employees"]}, "org": 2, "organisation": 2, "organization": 2, "core hr": 2},
    "compensation": {**{w: 3 for w in _DISCOVERY_WORDS["compensation"]}, "finances": 2, "salary structure": 4},
    "leave": {**{w: 3 for w in _DISCOVERY_WORDS["leave"]}, "time": 2, "requests": 1, "report": 1, "reports": 1},
}


def next_click(*, company, dataset: str, goal: str, url: str, title: str, options: list[dict], path: list[str],
               page_text: str = "", force: bool = False):
    """Which of the clickable things on this screen leads towards ``goal``. Asked once per screen while the agent
    learns a path; never while it reads (it then replays the confirmed path)."""
    prompt = json.dumps({"looking_for": goal, "screen": {"url": url, "title": title}, "clicked_so_far": path,
                         "can_click": [{k: o[k] for k in ("n", "role", "text") if k in o} for o in options],
                         "visible_text": page_text[:3000]}, ensure_ascii=False, indent=1)
    numbers = {o["n"] for o in options}

    def fallback():
        words = _NAVIGATION_WORDS.get(dataset, {})
        best, best_score = None, 0
        for o in options:
            text = normalize_header(o.get("text", "")) + " " + normalize_header(o.get("url") or "")
            score = sum(weight for w, weight in words.items() if re.search(rf"\b{re.escape(w)}\b", text))
            if dataset == "employees" and re.search(r"\b(leave|salary|contract|payroll|attendance|time)\b", text):
                score -= 4
            if dataset == "compensation" and re.search(r"\b(payslip|run|batch|loan|leave)\b", text):
                score -= 2
            if o.get("role") == "tab":
                score += 0.5  # a tab on the screen just opened is usually closer than the menu
            if score > best_score:
                best, best_score = o, score
        if best is None:
            return {"click": None, "reason": "nothing on this screen matches the words for this list"}
        return {"click": best["n"], "reason": f"'{best['text']}' matches the words for this list (keyword match)"}

    def validate(response):
        n = response.get("click")
        if n is not None and n not in numbers:
            return [{"level": "error", "message": f"AI chose {n!r}, which is not one of the things on the screen. Not used."}]
        return []

    return client.ask(purpose="page_discovery", system=NAVIGATION_SYSTEM, prompt=prompt, schema=NAVIGATION_SCHEMA,
                      fallback=fallback, validate=validate, company=company, force=force)


# =========================================================================== 5. reading a screen

EXTRACTION_SYSTEM = (
    "You read an HR tool's screen the way a person would. The page's visible text is given. Extract the records "
    "it shows as a table: column names as shown on screen and one row per record, copying every value exactly as "
    "it appears (do not convert, total or reformat anything). If the screen states a total number of records "
    "(for example 'Showing 1 to 10 of 23'), give that total, else null."
)
EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headers", "rows", "total_shown"],
    "properties": {
        "headers": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "total_shown": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
}


def extract_records(*, company, url: str, dataset: str, page_text: str, force: bool = False):
    text = page_text[:60000]
    prompt = json.dumps({"url": url, "looking_for": dataset, "page_text": text}, ensure_ascii=False)
    squashed = re.sub(r"\s+", " ", text)

    def fallback():
        return {"headers": [], "rows": [], "total_shown": None}

    def validate(response):
        missing = []
        for row in response.get("rows", []):
            for value in row:
                v = re.sub(r"\s+", " ", str(value)).strip()
                if v and v not in squashed:
                    missing.append(v)
        if missing:
            return [{"level": "error", "message": f"{len(missing)} values are not on the screen (for example {missing[0]!r}). Not used."}]
        return [{"level": "ok", "message": "Every value read by the AI appears on the screen."}]

    return client.ask(purpose="screen_extraction", system=EXTRACTION_SYSTEM, prompt=prompt, schema=EXTRACTION_SCHEMA,
                      fallback=fallback, validate=validate, company=company, force=force)

