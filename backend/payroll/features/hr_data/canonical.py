"""The standard payroll fields that any HR tool's columns are mapped onto.

The same field has different names in different tools ("Basic", "Basic Salary",
"Basic Pay"). Each target lists common names; the rule-based matcher uses them, and the
AI matcher sees the same list. A person confirms the mapping either way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .values import MONTHS


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    group: str  # employee | comp | revision | leave | attendance
    kind: str  # text | date | amount | number | bool | state | gender | period
    synonyms: tuple[str, ...]
    help: str = ""


TARGETS: list[Target] = [
    Target("employee.code", "Employee ID", "employee", "text",
           ("employee id", "emp id", "emp code", "employee code", "employee no", "emp no", "staff id", "id", "badge id",
            "employee number", "emp number", "staff code", "employee_id", "badge", "code")),
    Target("employee.name", "Employee name", "employee", "text",
           ("name", "employee name", "full name", "emp name", "employee", "staff name")),
    Target("employee.first_name", "First name", "employee", "text", ("first name", "given name", "firstname")),
    Target("employee.last_name", "Last name", "employee", "text", ("last name", "surname", "family name", "lastname")),
    Target("employee.work_state", "Work state", "employee", "state",
           ("state", "work state", "location state", "work location", "location", "branch state", "state of work", "office state")),
    Target("employee.gender", "Gender", "employee", "gender", ("gender", "sex")),
    Target("employee.date_of_joining", "Date of joining", "employee", "date",
           ("date of joining", "doj", "joining date", "joined on", "hire date", "date joined", "joining")),
    Target("employee.date_of_exit", "Date of leaving", "employee", "date",
           ("date of exit", "exit date", "leaving date", "date of leaving", "relieving date", "last working day", "termination date")),
    Target("employee.pf_applicable", "PF member (yes/no)", "employee", "bool", ("pf applicable", "pf member", "epf applicable", "pf")),
    Target("employee.esi_applicable", "ESI covered (yes/no)", "employee", "bool", ("esi applicable", "esi covered", "esi")),
    Target("employee.department", "Department (shown only)", "employee", "text", ("department", "dept")),
    Target("employee.designation", "Designation (shown only)", "employee", "text",
           ("designation", "job title", "title", "role", "job position", "position")),

    Target("comp.effective_from", "Salary effective from", "comp", "date",
           ("effective from", "effective date", "with effect from", "wef", "salary effective from", "applicable from",
            "valid from", "salary from", "contract start date", "start date")),
    Target("comp.recorded_on", "Salary recorded/approved on", "comp", "date",
           ("recorded on", "approved on", "entered on", "created on", "approval date", "updated on"),
           "When HR recorded this salary. Lets the system tell a raise made late (arrears) from one made on time."),
    Target("comp.basic", "Basic pay (monthly)", "comp", "amount",
           ("basic", "basic salary", "basic pay", "basic wage", "basic amount", "basic monthly"),
           "Only a column named after basic pay. A single wage or salary amount is the monthly gross."),
    Target("comp.da", "Dearness allowance (monthly)", "comp", "amount", ("da", "dearness allowance")),
    Target("comp.hra", "HRA (monthly)", "comp", "amount", ("hra", "house rent allowance", "house rent", "h r a")),
    Target("comp.special_allowance", "Special allowance (monthly)", "comp", "amount",
           ("special allowance", "special", "special allow", "spl allowance", "spl allow", "special pay")),
    Target("comp.conveyance", "Conveyance (monthly)", "comp", "amount", ("conveyance", "conveyance allowance", "transport allowance")),
    Target("comp.other_allowance", "Other allowance (monthly)", "comp", "amount",
           ("other allowance", "other allowances", "misc allowance", "miscellaneous allowance")),
    Target("comp.monthly_gross", "Monthly gross salary", "comp", "amount",
           ("gross", "monthly gross", "gross salary", "monthly salary", "salary", "monthly ctc", "ctc per month",
            "gross pay", "wage", "contract wage", "current salary", "monthly pay", "pay"),
           "The whole monthly pay as one amount (often called wage or contract wage). The company's salary structure splits it into basic, HRA and special allowance."),
    Target("comp.annual_ctc", "Annual CTC", "comp", "amount", ("ctc", "annual ctc", "annual salary", "yearly salary", "cost to company", "annual gross")),

    Target("revision.monthly_gross", "Revised monthly gross (raise on the same row)", "revision", "amount",
           ("revised salary", "new salary", "revised gross", "new monthly salary", "salary after raise", "revised monthly salary",
            "new gross", "revised pay", "new pay", "increment salary")),
    Target("revision.basic", "Revised basic", "revision", "amount", ("revised basic", "new basic")),
    Target("revision.hra", "Revised HRA", "revision", "amount", ("revised hra", "new hra")),
    Target("revision.special_allowance", "Revised special allowance", "revision", "amount", ("revised special allowance", "new special allowance")),
    Target("revision.effective_from", "Raise effective from", "revision", "date",
           ("revision effective from", "raise effective from", "new salary effective from", "revised from", "increment effective date",
            "raise start date", "revision date", "raise effective", "revision effective date", "raise from")),
    Target("revision.recorded_on", "Raise recorded/approved on", "revision", "date",
           ("raise approved on", "revision approved on", "revision recorded on", "raise recorded on")),

    Target("leave.start_date", "Leave start date", "leave", "date",
           ("from", "from date", "start date", "leave from", "start", "leave start", "leave start date", "start day")),
    Target("leave.end_date", "Leave end date", "leave", "date",
           ("to", "to date", "end date", "leave to", "end", "leave end", "leave end date", "end day")),
    Target("leave.date", "Leave date (single day)", "leave", "date", ("date", "leave date", "absent on", "absence date")),
    Target("leave.days", "Leave days", "leave", "number",
           ("days", "no of days", "number of days", "leave days", "total days", "requested days", "duration", "no. of days", "requested days")),
    Target("leave.type", "Leave type", "leave", "text", ("leave type", "type", "category", "leave category", "leave name")),
    Target("leave.status", "Leave status", "leave", "text", ("status", "approval status", "leave status")),
    Target("leave.unpaid", "Unpaid leave (yes/no)", "leave", "bool", ("unpaid", "is unpaid", "loss of pay", "is lop")),

    # Daily attendance: one row per person per day they checked in. Used only to suggest unpaid absences.
    Target("presence.date", "Day present (attendance)", "presence", "date",
           ("attendance date", "date", "in date", "check in date", "clock in date", "punch date", "work date", "present on",
            "attendance day", "day"),
           "A day the person checked in. Days with no check-in and no approved leave are suggested as possible unpaid absences."),
    Target("presence.check_in", "Check-in time (shown only)", "presence", "text",
           ("check in", "in time", "clock in", "punch in", "first in", "time in")),
    Target("presence.check_out", "Check-out time (shown only)", "presence", "text",
           ("check out", "out time", "clock out", "punch out", "last out", "time out")),

    Target("attendance.period", "Month", "attendance", "period", ("month", "period", "payroll month", "pay period", "salary month")),
    Target("attendance.unpaid_days", "Unpaid days in that month", "attendance", "number",
           ("unpaid days", "lop days", "loss of pay days", "lwp days", "unpaid leave days", "unpaid leave", "lop")),
]

TARGETS_BY_KEY = {t.key: t for t in TARGETS}
IGNORE = "ignore"
MONTH_TARGET_PREFIX = "attendance.unpaid_days@"
COMP_COMPONENTS = ["basic", "da", "hra", "special_allowance", "conveyance", "other_allowance"]
GROUP_LABELS = {
    "employee": "Employee",
    "comp": "Salary",
    "revision": "Salary change on the same row",
    "leave": "Leave",
    "attendance": "Unpaid days per month",
    "presence": "Attendance (days present)",
}


def target_info(key: str) -> dict:
    if key.startswith(MONTH_TARGET_PREFIX):
        return {"key": key, "label": f"Unpaid days in {key.split('@', 1)[1]}", "group": "attendance", "kind": "number"}
    if key == IGNORE:
        return {"key": key, "label": "Not used", "group": "", "kind": ""}
    t = TARGETS_BY_KEY.get(key)
    if not t:
        return {"key": key, "label": key, "group": "", "kind": "unknown"}
    return {"key": t.key, "label": t.label, "group": t.group, "kind": t.kind, "help": t.help}


def is_valid_target(key: str) -> bool:
    if key == IGNORE or key in TARGETS_BY_KEY:
        return True
    if key.startswith(MONTH_TARGET_PREFIX):
        return bool(re.fullmatch(r"\d{4}-\d{2}", key.split("@", 1)[1]))
    return False


def catalogue() -> list[dict]:
    return [
        {"key": t.key, "label": t.label, "group": t.group, "group_label": GROUP_LABELS[t.group], "kind": t.kind,
         "synonyms": list(t.synonyms), "help": t.help}
        for t in TARGETS
    ]


def normalize_header(text: str) -> str:
    text = str(text).lower()
    text = re.sub(r"\((?:₹|rs\.?|inr|in ₹|in rs\.?|monthly|per month|p\.m\.)\)", " ", text)
    text = text.replace("₹", " ").replace("_", " ").replace("#", " no ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def month_column(header: str, default_year: int | None) -> str | None:
    """'June LOP days', 'Unpaid leave - Jul 2025' -> 'attendance.unpaid_days@2025-07'."""
    norm = normalize_header(header)
    if not re.search(r"\b(lop|unpaid|lwp|loss of pay|leave)\b", norm):
        return None
    month = None
    for word in norm.split():
        if word in MONTHS:
            month = MONTHS[word]
            break
    if month is None:
        return None
    year_match = re.search(r"\b(20\d{2})\b", norm)
    year = int(year_match.group(1)) if year_match else default_year
    if year is None:
        return None
    return f"{MONTH_TARGET_PREFIX}{year:04d}-{month:02d}"


def heuristic_mapping(headers: list[str], dataset_hint: str = "", default_year: int | None = None) -> dict[str, dict]:
    """Rule-based suggestion: {header: {"target", "confidence", "reason"}}. Used when AI is off, and as a cross-check."""
    scored: dict[str, list[tuple[float, str, str]]] = {}
    for header in headers:
        norm = normalize_header(header)
        options: list[tuple[float, str, str]] = []
        month_target = month_column(header, default_year)
        if month_target:
            options.append((0.95, month_target, "column names a month and unpaid leave"))
        for t in TARGETS:
            for syn in t.synonyms:
                if norm == syn:
                    options.append((1.0, t.key, f"same name as '{syn}'"))
                elif len(syn) > 3 and re.search(rf"\b{re.escape(syn)}\b", norm):
                    options.append((0.6 + 0.3 * len(syn) / max(len(norm), 1), t.key, f"contains '{syn}'"))
        scored[header] = sorted(options, key=lambda o: -o[0])

    # Which kind of table is this? Count confident matches per group.
    group_votes: dict[str, float] = {}
    for opts in scored.values():
        if opts and opts[0][0] >= 0.9:
            group = target_info(opts[0][1])["group"]
            group_votes[group] = group_votes.get(group, 0) + 1
    hint_groups = {"employees": ("employee",), "compensation": ("comp",), "salary": ("comp",), "leave": ("leave",),
                   "attendance": ("presence", "attendance")}.get(dataset_hint, ())
    for hint_group in hint_groups:
        group_votes[hint_group] = group_votes.get(hint_group, 0) + 2

    result: dict[str, dict] = {}
    used: set[str] = set()
    # Assign the most confident headers first so they claim their target.
    order = sorted(headers, key=lambda h: -(scored[h][0][0] if scored[h] else 0))
    for header in order:
        best = None
        for confidence, key, reason in scored[header]:
            if key in used and not key.startswith(MONTH_TARGET_PREFIX):
                continue
            group = target_info(key)["group"]
            # Ambiguous words like "From", "Status", "Type" lean to the table's main kind.
            adjusted = confidence + 0.05 * group_votes.get(group, 0)
            if group in ("leave",) and "leave" not in group_votes and confidence < 1.0:
                adjusted -= 0.3
            if best is None or adjusted > best[0]:
                best = (adjusted, key, reason, confidence)
        if best and best[3] >= 0.6:
            used.add(best[1])
            result[header] = {"target": best[1], "confidence": round(min(best[3], 1.0), 2), "reason": best[2]}
        else:
            result[header] = {"target": IGNORE, "confidence": 0.0, "reason": "no known payroll field matches this name"}

    # A group only makes sense with its core fields; drop stray matches such as an
    # employee table's "Status" column looking like a leave status.
    assigned = {v["target"] for v in result.values()}
    incomplete = set()
    if not ({"leave.start_date", "leave.date"} & assigned and {"leave.type", "leave.days", "leave.unpaid"} & assigned):
        incomplete.add("leave")
    if not ({"revision.monthly_gross", "revision.basic"} & assigned):
        incomplete.add("revision")
    if not ({f"comp.{c}" for c in COMP_COMPONENTS} | {"comp.monthly_gross", "comp.annual_ctc"}) & assigned:
        incomplete.add("comp")
    if "attendance.unpaid_days" not in assigned:
        incomplete.add("attendance-period")
    if "presence.date" not in assigned:
        incomplete.add("presence")
    taken = {v["target"] for v in result.values()}
    for header, info in result.items():
        group = target_info(info["target"])["group"]
        if group in incomplete or (info["target"] == "attendance.period" and "attendance-period" in incomplete):
            # fall back to the next-best field from a group that does make sense here
            alternative = next(((c, k, r) for c, k, r in scored[header]
                                if target_info(k)["group"] not in incomplete and k not in taken and k != "attendance.period"), None)
            if alternative and alternative[0] >= 0.6:
                result[header] = {"target": alternative[1], "confidence": round(min(alternative[0], 1.0), 2), "reason": alternative[2]}
                taken.add(alternative[1])
            else:
                result[header] = {"target": IGNORE, "confidence": 0.0,
                                  "reason": f"looked like '{target_info(info['target'])['label']}' but the table has no matching fields to go with it"}
    return {h: result[h] for h in headers}
