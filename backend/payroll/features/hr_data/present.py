from __future__ import annotations

import re

from payroll_engine.money import to_str
from payroll_engine.periods import Period

from payroll.shared.present import _dt, _user

from ..ai.client import MODEL_PROVIDERS
from ..ai.present import ai
from ..payroll_runs.summary import run_summary
from . import profiles
from .canonical import catalogue
from .normalize import unpaid_days_for


def employee(e):
    return {"code": e.code, "name": e.name, "work_state": e.work_state, "gender": e.gender,
            "date_of_joining": e.date_of_joining and e.date_of_joining.isoformat(),
            "date_of_exit": e.date_of_exit and e.date_of_exit.isoformat(), "active": e.active}


def connector(c):
    return {
        # A profile counts only while profiles are switched on (HR_TOOL_PROFILES); otherwise the tool shows as unknown.
        "id": c.pk, "name": c.name, "kind": c.kind, "system_name": c.system_name,
        "profile": c.profile if profiles.profile_for(c.profile) else "", "base_url": c.base_url, "username": c.username,
        "has_password": bool(c.password), "recipe": c.recipe, "recipe_confirmed_by": _user(c.recipe_confirmed_by),
        "recipe_confirmed_at": _dt(c.recipe_confirmed_at), "recipe_ai": ai(c.recipe_ai),
        "last_job": job(c.jobs.order_by("-id").first()) if c.jobs.exists() else None,
    }


def _read_log(entries):
    for entry in entries:
        message = entry["message"]
        for provider in MODEL_PROVIDERS:
            message = message.replace(f"field mapping suggested by {provider}", "field mapping suggested by the model")
        yield {**entry, "message": message}


def job(j, detail=False):
    if j is None:
        return None
    out = {
        "id": j.pk, "kind": j.kind, "source_system": j.source_system, "status": j.status, "status_label": j.get_status_display(),
        "original_filename": j.original_filename, "error": j.error, "created_at": _dt(j.created_at), "started_at": _dt(j.started_at),
        "finished_at": _dt(j.finished_at), "started_by": _user(j.started_by), "table_count": j.tables.count(),
        "snapshot_id": j.snapshots.order_by("-id").values_list("id", flat=True).first(),
    }
    if detail:
        out["log"] = list(_read_log(j.log))
        out["stats"] = {k: v for k, v in (j.stats or {}).items() if k != "heartbeat"}
        out["tables"] = [table(t) for t in j.tables.select_related("mapping", "mapping__suggested_by", "mapping__confirmed_by")]
        _show_salary_with_salary(out["tables"])
        out["catalogue"] = catalogue()
    return out


SALARY_HEADER = re.compile(r"\b(salary|salaries|wage|wages|ctc|basic|gross)\b", re.IGNORECASE)


def _show_salary_with_salary(tables: list[dict]):
    """A salary figure on an employee list (e.g. Horilla's "Salary" on each employee's page) is shown with the
    salary data, not among the employee columns, when the read has its own salary list and the column is not used.
    Without a salary list such a column may be the only salary there is, so it stays where it is."""
    salary_tables = [t for t in tables if t["dataset_hint"] == "compensation"]
    if not salary_tables:
        return
    for t in tables:
        if t["dataset_hint"] != "employees" or not t["mapping"]:
            continue
        used = t["mapping"]["mapping"]
        columns = [h for h in t["headers"] if SALARY_HEADER.search(h) and used.get(h, "ignore") == "ignore"]
        if not columns:
            continue
        t["salary_columns"] = columns
        who = next((h for h in t["headers"] if used.get(h) in ("employee.code", "employee.name")), t["headers"][0])
        salary_tables[0].setdefault("salary_from_employees", []).append({
            "table": t["name"], "columns": columns,
            "rows": [{"employee": r.get(who, ""), "values": {c: r.get(c, "") for c in columns}} for r in t["rows"]],
        })


def table(t):
    m = t.mapping
    return {
        "id": t.pk, "name": t.name, "dataset_hint": t.dataset_hint, "source_url": t.source_url, "headers": t.headers,
        "row_count": len(t.rows), "rows": t.rows[:200], "read_info": t.read_info,
        "mapping": None if m is None else {
            "id": m.pk, "mapping": m.mapping, "value_maps": m.value_maps, "options": m.options,
            "confirmed": bool(m.confirmed_at), "confirmed_by": _user(m.confirmed_by), "confirmed_at": _dt(m.confirmed_at),
            "suggestion": (m.options or {}).get("suggestion"), "suggested_by": ai(m.suggested_by),
            "reused": bool(m.confirmed_at and m.confirmed_at < t.job.created_at),
        },
    }


def snapshot_summary(s):
    if s is None:
        return None
    levels = [c.get("level") for c in s.checks]
    return {
        "id": s.pk, "source_kind": s.source_kind, "source_system": s.source_system, "status": s.status,
        "status_label": s.get_status_display(), "created_at": _dt(s.created_at), "reviewed_by": _user(s.reviewed_by),
        "reviewed_at": _dt(s.reviewed_at), "employee_count": len(s.data.get("employees", [])), "content_hash": s.content_hash,
        "errors": levels.count("error"), "warnings": levels.count("warning"), "job_id": s.job_id,
        # People missing a fact pay depends on: they wait, everyone else can be paid.
        "incomplete": s.data.get("incomplete") or {},
        # Read by the agent on its own, for the people waiting for HR data (not by a person pressing "Read").
        "read_for_waiting": ((s.job.stats or {}).get("for_waiting") or []) if s.job_id else [],
    }


def snapshot(s):
    out = snapshot_summary(s)
    data = s.data
    periods = sorted({r["period"] for r in data.get("attendance", [])} | {
        str(p) for r in data.get("leave", []) if r["unpaid"]
        for p in _periods(r["start_date"], r["end_date"])})
    out.update({
        "data": data,
        "checks": s.checks,
        "review_note": s.review_note,
        "unpaid_by_month": {
            "periods": periods,
            "rows": [{"code": e["code"], "name": e["name"],
                      "days": {p: to_str(unpaid_days_for(data, e["code"], Period.parse(p))) for p in periods}}
                     for e in data.get("employees", [])],
        },
        "used_by_runs": [run_summary(r) for r in s.runs.order_by("period")],
    })
    return out


def _periods(start, end):
    p, last = Period.parse(start[:7]), Period.parse(end[:7])
    while p <= last:
        yield p
        p = p.next()
