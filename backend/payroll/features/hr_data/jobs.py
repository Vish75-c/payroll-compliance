"""Reading jobs: spreadsheet uploads and browser-agent reads both produce RawTables, then share one path."""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from payroll_engine.hashing import sha256

from payroll.shared.permissions import RuleViolation, require_role

from ..ai import tasks as ai_tasks
from . import profiles
from .canonical import IGNORE, heuristic_mapping, normalize_header, target_info
from .models import Connector, FieldMapping, IngestionJob, RawTable
from .normalize import default_value_maps, validate_mapping
from .spreadsheet import read_spreadsheet

# Which payroll fields belong in each kind of table, so an exact column name is only trusted where it makes sense
# (an "End Date" on employee pages is the exit date's neighbour, not a leave's end).
DATASET_TARGET_GROUPS = {"employees": ("employee",), "compensation": ("comp", "revision"), "leave": ("leave",),
                         "attendance": ("presence", "attendance")}
# Fields a column of each kind of table may be given at all (an employee list can carry a salary, never a leave).
DATASET_ALLOWED_GROUPS = {"employees": ("employee", "comp", "revision"), "compensation": ("employee", "comp", "revision"),
                          "leave": ("employee", "leave"), "attendance": ("employee", "presence", "attendance")}

# Leave statuses that mean the leave never happened; counting them would cut pay for days that were worked.
LEAVE_NEVER_TAKEN = ("reject", "cancel", "withdrawn", "declined", "refused")

log = logging.getLogger("payroll.ingestion")
_running: dict[int, threading.Thread] = {}


def fingerprint(source_system: str, headers: list[str]) -> str:
    return sha256({"system": source_system.lower(), "headers": sorted(normalize_header(h) for h in headers)})


def job_log(job: IngestionJob, message: str, level: str = "info", **extra):
    entry = {"at": timezone.now().isoformat(), "level": level, "message": message, **extra}
    job.log = list(job.log or []) + [entry]
    stats = dict(job.stats or {})
    stats["heartbeat"] = entry["at"]
    job.stats = stats
    job.save(update_fields=["log", "stats"])
    getattr(log, "warning" if level in ("warning", "error") else "info")("job %s: %s", job.pk, message)


def store_tables(job: IngestionJob, tables: list[dict]) -> list[RawTable]:
    created = []
    for t in tables:
        created.append(RawTable.objects.create(
            job=job, name=t["name"][:200], dataset_hint=t.get("dataset_hint", ""), source_url=t.get("source_url", "")[:500],
            headers=t["headers"], rows=t["rows"], fingerprint=fingerprint(job.source_system, t["headers"]),
            read_info=t.get("read_info", {}),
        ))
    apply_saved_mappings(job)
    return created


def apply_saved_mappings(job: IngestionJob):
    """Reuse a mapping this company already confirmed; otherwise pre-fill one from a known HR tool's
    profile. Neither asks the AI. A pre-filled mapping still waits for a person to confirm it."""
    for table in job.tables.all():
        if table.mapping and table.mapping.confirmed_at:
            continue
        if not _reuse_confirmed(job, table) and table.mapping is None:
            _prefill_from_profile(job, table)
    update_status(job)


def _reuse_confirmed(job: IngestionJob, table: RawTable) -> bool:
    saved = FieldMapping.objects.filter(company=job.company, source_system=job.source_system, fingerprint=table.fingerprint,
                                        confirmed_at__isnull=False).order_by("-confirmed_at").first()
    if saved:
        table.mapping = saved
        table.save(update_fields=["mapping"])
        job_log(job, f"'{table.name}': using the field mapping confirmed on {timezone.localtime(saved.confirmed_at):%d %b %Y} (no AI needed).")
        _compare_with_profile(job, table, saved)
        return True
    older = FieldMapping.objects.filter(company=job.company, source_system=job.source_system, table_name=table.name,
                                        confirmed_at__isnull=False).order_by("-confirmed_at").first()
    if not older:
        return False
    needed = [h for h, t in older.mapping.items() if t != IGNORE]
    missing = [h for h in needed if h not in table.headers]
    if len(missing) * 2 > len(needed):
        # Most columns differ from the table of the same name read before (e.g. an HR tool's export
        # uploaded as "Leave"): a new layout that needs its own mapping, not a changed screen.
        return False
    if not missing:
        mapping = {h: older.mapping.get(h, IGNORE) for h in table.headers}
        new_cols = [h for h in table.headers if h not in older.mapping]
        useful, profiled = _meaning_of_new_columns(job, table, mapping, new_cols)
        # New columns no known HR tool profile explains go to the model (or, with AI off, the rule-based matcher):
        # a person confirmed the old columns, not these, so nobody has said yet what they mean.
        ask_model = [] if profiled else list(new_cols)  # exact names are only a pre-fill until the model has answered
        if useful or ask_model:
            mapping.update(useful)
            fm = FieldMapping.objects.create(
                company=job.company, source_system=job.source_system, table_name=table.name, fingerprint=table.fingerprint,
                headers=table.headers, mapping=mapping, value_maps=default_value_maps(table.rows, mapping),
                options={**(older.options or {}), "ask_model_for": ask_model,
                         "suggestion": {h: {"target": t, "confidence": 1.0, "reason": "new column"} for h, t in useful.items()}},
            )
            table.mapping = fm
            table.save(update_fields=["mapping"])
            names = ", ".join(f"'{h}' as {target_info(t)['label']}" for h, t in useful.items())
            if ask_model:
                what = (f"{len(ask_model)} ({', '.join(ask_model[:6])}{'…' if len(ask_model) > 6 else ''}) for the model to suggest"
                        + (f"; pre-filled by name until then: {names}" if useful else ""))
            else:
                what = names
            _add_issue(table, "new_columns", f"New column(s) since the mapping was confirmed: {what}. "
                       + ("Press Suggest mapping, then " if ask_model else "") + "check and confirm the mapping.")
            job_log(job, f"'{table.name}': new column(s) since the mapping was confirmed: {what}. A person must confirm the mapping.", "warning")
            return True
        reused = FieldMapping.objects.create(
            company=job.company, source_system=job.source_system, table_name=table.name, fingerprint=table.fingerprint,
            headers=table.headers, mapping=mapping, value_maps=older.value_maps, options=older.options,
            confirmed_by=older.confirmed_by, confirmed_at=older.confirmed_at,
        )
        table.mapping = reused
        table.save(update_fields=["mapping"])
        job_log(job, f"'{table.name}': the screen has new columns ({', '.join(new_cols)}); they are not used. "
                     "All columns needed by the confirmed mapping are still there.", "warning")
        return True
    _add_issue(table, "screen_changed",
               f"The screen changed since the mapping was confirmed: column(s) {', '.join(missing)} are gone. Confirm the mapping again.")
    job_log(job, f"'{table.name}': screen changed, missing column(s) {', '.join(missing)}. A person must confirm the mapping again.", "warning")
    return False


def _meaning_of_new_columns(job: IngestionJob, table: RawTable, mapping: dict, new_cols: list[str]) -> tuple[dict[str, str], bool]:
    """New columns the HR tool's profile (or an exact field name) gives a meaning to, for fields not yet used, and
    whether a profile matched (then a column it leaves out is known not to be needed)."""
    if not new_cols:
        return {}, False
    system = (job.connector.profile or job.connector.system_name) if job.connector else ""
    match = profiles.match_layout(table.headers, table.dataset_hint, system=system)
    known = match.mapping if match else {}
    # Without a profile (an unknown tool, or profiles switched off), a column named exactly like a payroll field of
    # this kind of table counts too: "Gender" on employee pages is the employee's gender, so it must not be dropped.
    groups = DATASET_TARGET_GROUPS.get(table.dataset_hint, ())
    exact = heuristic_mapping(new_cols, table.dataset_hint)
    for h in new_cols:
        guess = exact.get(h) or {}
        if h not in known and guess.get("confidence", 0) >= 1.0 and guess["target"].split(".")[0] in groups:
            known[h] = guess["target"]
    taken = {t for t in mapping.values() if t != IGNORE}
    out = {}
    for h in new_cols:
        target = known.get(h, IGNORE)
        if target != IGNORE and target not in taken:
            out[h] = target
            taken.add(target)
    return out, match is not None


def _prefill_from_profile(job: IngestionJob, table: RawTable):
    system = (job.connector.profile or job.connector.system_name) if job.connector else ""
    match = profiles.match_layout(table.headers, table.dataset_hint, system=system)
    if not match:
        return
    lay = match.layout
    fm = FieldMapping.objects.create(
        company=job.company, source_system=job.source_system, table_name=table.name, fingerprint=table.fingerprint,
        headers=table.headers, mapping=match.mapping, value_maps=default_value_maps(table.rows, match.mapping),
        options={"date_order": "DMY", "suggestion": match.suggestion, "profile": lay.ref, "profile_name": lay.system_name,
                 "profile_notes": list(lay.notes)},
    )
    table.mapping = fm
    table.save(update_fields=["mapping"])
    for column, how_to_fix in match.missing_needs.items():
        _add_issue(table, "profile_missing_column", f"No '{column}' column. {how_to_fix}")
    job_log(job, f"'{table.name}': recognised as {lay.system_name} {lay.kind} '{lay.id}'; field mapping pre-filled from the "
                 f"{lay.system_name} profile (no AI). Waiting for a person to confirm."
                 + (f" Missing column(s): {', '.join(match.missing_needs)}." if match.missing_needs else ""),
            "warning" if match.missing_needs else "info")


def _compare_with_profile(job: IngestionJob, table: RawTable, saved: FieldMapping):
    """A company's own mapping wins, but if it disagrees with what the HR tool's profile says a column means,
    say so on every read: it may have been a mistake (or a deliberate choice, which the maker can keep)."""
    system = (job.connector.profile or job.connector.system_name) if job.connector else ""
    match = profiles.match_layout(table.headers, table.dataset_hint, system=system)
    if not match:
        return
    listed = {normalize_header(c) for c in match.layout.columns}
    differ = [(h, saved.mapping.get(h, IGNORE), match.mapping[h]) for h in table.headers
              if normalize_header(h) in listed and saved.mapping.get(h, IGNORE) != match.mapping[h]]
    if not differ:
        return
    label = lambda t: target_info(t)["label"]  # noqa: E731
    _add_issue(table, "differs_from_profile",
               f"The mapping confirmed for this company differs from the {match.layout.system_name} profile: "
               + "; ".join(f"'{h}' is {label(mine)} here, the profile says {label(theirs)}" for h, mine, theirs in differ)
               + ". If that was not on purpose, change it and confirm again.")


def _add_issue(table: RawTable, code: str, message: str):
    info = dict(table.read_info)
    info["issues"] = [i for i in info.get("issues", []) if i.get("code") != code or i.get("message") != message]
    info["issues"].append({"level": "warning", "code": code, "message": message})
    table.read_info = info
    table.save(update_fields=["read_info"])


def update_status(job: IngestionJob):
    if job.status in ("failed", "done", "running", "queued"):
        return
    tables = list(job.tables.all())
    if not tables:
        return
    all_mapped = all(t.mapping_id and t.mapping.confirmed_at for t in tables)
    job.status = "ready" if all_mapped else "needs_mapping"
    job.save(update_fields=["status"])


def suggest(job: IngestionJob, user, force: bool = False):
    require_role(user, "maker", "map fields")
    year = timezone.localdate().year
    for table in job.tables.all():
        pending = table.mapping and not table.mapping.confirmed_at and table.mapping.options.get("ask_model_for")
        if pending and not force:
            _suggest_new_columns(job, table)
            continue
        if table.mapping and (table.mapping.confirmed_at or table.mapping.options.get("profile")) and not force:
            continue  # confirmed, or pre-filled from a known HR tool's profile
        interaction = ai_tasks.suggest_mapping(
            company=job.company, source_system=job.source_system, table_name=table.name, headers=table.headers,
            sample_rows=table.rows[:3], dataset_hint=table.dataset_hint, default_year=year, force=force,
        )
        suggestion = ai_tasks.mapping_from_interaction(interaction, table.headers)
        for h, s in suggestion.items():
            # A column that is 0 or empty on every row (e.g. an unfilled "Basic Salary" in an employee export)
            # carries nothing; using it would give everyone a zero salary.
            if s["target"] != IGNORE and all(str(r.get(h, "")).strip() in ("", "0", "0.0", "0.00") for r in table.rows):
                suggestion[h] = {**s, "target": IGNORE, "confidence": 0.9, "reason": "0 or empty on every row, so not used"}
        mapping = {h: s["target"] for h, s in suggestion.items()}
        fm = FieldMapping.objects.create(
            company=job.company, source_system=job.source_system, table_name=table.name, fingerprint=table.fingerprint,
            headers=table.headers, mapping=mapping, value_maps=default_value_maps(table.rows, mapping),
            options={"date_order": "DMY", "suggestion": suggestion}, suggested_by=interaction,
        )
        table.mapping = fm
        table.save(update_fields=["mapping"])
        job_log(job, f"'{table.name}': field mapping suggested by {interaction.provider}"
                     + (" (saved answer reused)" if getattr(interaction, "reused", False) else "") + ". Waiting for a person to confirm.")
    update_status(job)


def _empty_everywhere(table: RawTable, header: str) -> bool:
    return all(str(r.get(header, "")).strip() in ("", "0", "0.0", "0.00") for r in table.rows)


def _suggest_new_columns(job: IngestionJob, table: RawTable):
    """The model suggests what the new columns of a known screen mean. It sees the whole table for context, but only
    its answers for the new columns are taken: the old columns keep what a person confirmed."""
    fm = table.mapping
    ask = fm.options["ask_model_for"]
    interaction = ai_tasks.suggest_mapping(
        company=job.company, source_system=job.source_system, table_name=table.name, headers=table.headers,
        sample_rows=table.rows[:3], dataset_hint=table.dataset_hint, default_year=timezone.localdate().year,
    )
    suggestion = ai_tasks.mapping_from_interaction(interaction, table.headers)
    taken = {t for h, t in fm.mapping.items() if h not in ask and t != IGNORE}
    mapping, notes = dict(fm.mapping), dict(fm.options.get("suggestion") or {})
    for h in ask:
        s = suggestion.get(h) or {"target": IGNORE, "confidence": 0, "reason": "no suggestion"}
        allowed = DATASET_ALLOWED_GROUPS.get(table.dataset_hint)
        if s["target"] != IGNORE and allowed and s["target"].split(".")[0] not in allowed:
            s = {**s, "target": IGNORE, "reason": f"suggested {target_info(s['target'])['label']}, which does not belong in this table"}
        if s["target"] != IGNORE and _empty_everywhere(table, h):
            s = {**s, "target": IGNORE, "confidence": 0.9, "reason": "0 or empty on every row, so not used"}
        if s["target"] in taken:  # already given by a confirmed column: keep the person's choice
            s = {**s, "target": IGNORE, "reason": f"{s['reason']} (already taken by a confirmed column)"}
        mapping[h] = s["target"]
        notes[h] = s
        if s["target"] != IGNORE:
            taken.add(s["target"])
    fm.mapping, fm.suggested_by = mapping, interaction
    fm.value_maps = default_value_maps(table.rows, mapping)
    fm.options = {**fm.options, "ask_model_for": [], "suggestion": notes}
    fm.save(update_fields=["mapping", "suggested_by", "value_maps", "options"])
    used = [f"'{h}' as {target_info(mapping[h])['label']}" for h in ask if mapping[h] != IGNORE]
    job_log(job, f"'{table.name}': {interaction.provider} suggested the {len(ask)} new column(s): "
                 + (", ".join(used) if used else "none is needed for payroll") + ". The confirmed columns are kept. Waiting for a person to confirm.")


@transaction.atomic
def confirm(table: RawTable, user, mapping: dict, value_maps: dict | None = None, options: dict | None = None) -> FieldMapping:
    require_role(user, "maker", "confirm a field mapping")
    mapping = {h: mapping.get(h, IGNORE) or IGNORE for h in table.headers}
    errors = validate_mapping(mapping, table.headers)
    if errors:
        raise RuleViolation("; ".join(errors))
    base = table.mapping
    value_maps = value_maps if value_maps is not None else default_value_maps(table.rows, mapping)
    wrongly_counted = sorted(status for status, use in (value_maps.get("leave.status") or {}).items()
                             if use == "count" and any(word in status.lower() for word in LEAVE_NEVER_TAKEN))
    if wrongly_counted:
        raise RuleViolation(
            "Leave with the status " + ", ".join(f"'{s}'" for s in wrongly_counted) + " was not taken, so it cannot count "
            "towards unpaid days. Set it to Ignore.", code="leave_status_counted")
    opts = dict((base.options if base else {}) or {})
    opts.update(options or {})
    opts.setdefault("date_order", "DMY")
    fm = FieldMapping.objects.create(
        company=table.job.company, source_system=table.job.source_system, table_name=table.name, fingerprint=table.fingerprint,
        headers=table.headers, mapping=mapping, value_maps=value_maps, options=opts,
        suggested_by=base.suggested_by if base else None, confirmed_by=user, confirmed_at=timezone.now(),
    )
    for same in RawTable.objects.filter(job=table.job, fingerprint=table.fingerprint):
        same.mapping = fm
        info = dict(same.read_info)
        info["issues"] = [i for i in info.get("issues", []) if i.get("code") != "differs_from_profile"]
        same.read_info = info
        same.save(update_fields=["mapping", "read_info"])
        _compare_with_profile(table.job, same, fm)
    if fm.suggested_by:
        ai = fm.suggested_by
        suggested = {c["column"]: c["target"] for c in (ai.response or {}).get("columns", [])}
        edited = [h for h in table.headers if suggested.get(h, IGNORE) != mapping[h]]
        ai.status = "accepted"
        ai.reviewed_by = user
        ai.reviewed_at = timezone.now()
        # One answer is reused for the same screen read after read; keep one review note on it, not one per confirmation.
        earlier = [v for v in (ai.validation or []) if v.get("code") == "confirmed" or str(v.get("message", "")).startswith("Confirmed by ")]
        times = sum(v.get("times", 1) for v in earlier) + 1
        ai.validation = [v for v in (ai.validation or []) if v not in earlier] + [{
            "level": "info", "code": "confirmed", "times": times,
            "message": f"Confirmed by {user.username}" + (f"; changed {len(edited)} column(s): {', '.join(edited)}" if edited else " without changes")
                       + (f" (confirmed {times} times, latest {timezone.localtime():%d %b %Y})" if times > 1 else "")}]
        ai.save(update_fields=["status", "reviewed_by", "reviewed_at", "validation"])
    job_log(table.job, f"'{table.name}': field mapping confirmed by {user.username}. Later reads of this screen reuse it without asking the AI.")
    update_status(table.job)
    return fm


# --------------------------------------------------------------------------- spreadsheet

UPLOAD_DATASETS = {"": "", "employees": "Employees", "compensation": "Salary", "leave": "Leave", "attendance": "Attendance"}


def _read_files(job: IngestionJob, files: list[tuple[str, bytes, str]]) -> tuple[list[dict], list[str]]:
    """``files``: (filename, content, dataset). A file uploaded for one kind of data ("employees",
    "compensation", "leave") has every sheet tagged with it; dataset "" means the file may hold anything."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    tables, problems = [], []
    taken = set(job.tables.values_list("name", flat=True))
    saved = list((job.stats or {}).get("files", []))
    for filename, content, dataset in files:
        if dataset not in UPLOAD_DATASETS:
            raise RuleViolation(f"Unknown kind of data: {dataset}")
        digest = hashlib.sha256(content).hexdigest()
        same = next((f for f in saved if f.get("sha256") == digest), None)
        if same:
            # The same rows twice would count every leave day twice.
            problems.append(f"{filename}: the same file ({same['name']}) is already in this read, so it was not added again")
            continue
        try:
            found = read_spreadsheet(filename, content)
        except Exception as exc:  # noqa: BLE001 - any unreadable file is reported to the user
            problems.append(f"{filename}: could not read the file ({exc})")
            continue
        if not found:
            problems.append(f"{filename}: no table with column headings was found")
            continue
        # The same rows again (e.g. the same list exported twice: the bytes differ, the data does not) would
        # count every leave day twice: a table identical to one already in this read is left out.
        seen_tables = {sha256({"h": t.headers, "r": t.rows}) for t in job.tables.all()} | {sha256({"h": t["headers"], "r": t["rows"]}) for t in tables}
        fresh = [t for t in found if sha256({"h": t["headers"], "r": t["rows"]}) not in seen_tables]
        if not fresh:
            problems.append(f"{filename}: the same data is already in this read, so it was not added again")
            continue
        found = fresh
        label = UPLOAD_DATASETS[dataset]
        for t in found:
            # A sheet uploaded as "Leave" is called "Leave" whatever the file is called this month.
            name = (label if len(found) == 1 else f"{label} · {t['name']}") if label else t["name"]
            unique, n = name, 1
            while unique in taken:
                n += 1
                unique = f"{name} ({n})"
            taken.add(unique)
            t["name"] = unique
            t["dataset_hint"] = dataset
            t["read_info"] = {**t["read_info"], "file": filename}
            tables.append(t)
        path = default_storage.save(f"uploads/{filename}", ContentFile(content))
        saved.append({"name": filename, "dataset": dataset, "path": path, "sha256": digest, "tables": [t["name"] for t in found]})
    job.stats = {**(job.stats or {}), "files": saved}
    names = [f["name"] for f in saved]
    job.original_filename = ", ".join(names)[:255]
    if names and not job.file:
        job.file.name = saved[0]["path"]
    job.save(update_fields=["stats", "original_filename", "file"])
    return tables, problems


def _describe(tables: list[dict]) -> str:
    return ", ".join(f"{t['name']} ({len(t['rows'])} rows, from {t['read_info']['file']})" for t in tables)


def run_spreadsheet_job(company, user, files: list[tuple[str, bytes, str]], connector: Connector | None = None) -> IngestionJob:
    """One read from one or more files: a workbook with everything, or a file per kind of data."""
    if not files:
        raise RuleViolation("Choose at least one file to upload.")
    job = IngestionJob.objects.create(company=company, connector=connector, kind="spreadsheet", source_system="Spreadsheet",
                                      status="running", started_by=user, started_at=timezone.now())
    tables, problems = _read_files(job, files)
    job.finished_at = timezone.now()
    if not tables:
        job.status = "failed"
        job.error = "; ".join(problems) or "No table with column headings was found."
        job.save()
        return job
    job.status = "needs_mapping"
    job.save()
    job_log(job, f"Read {len(tables)} table(s): {_describe(tables)}")
    for problem in problems:
        job_log(job, problem, "error")
    store_tables(job, tables)
    return job


def add_spreadsheet_files(job: IngestionJob, user, files: list[tuple[str, bytes, str]]) -> IngestionJob:
    """Add files to a read that has no snapshot yet, e.g. the leave file that came a day after the salary file."""
    require_role(user, "maker", "upload HR data")
    if job.kind != "spreadsheet":
        raise RuleViolation("Files can only be added to a spreadsheet upload.")
    if job.status == "done" or job.snapshots.exists():
        raise RuleViolation("A data snapshot was already built from this read. Upload the files as a new read.")
    if not files:
        raise RuleViolation("Choose at least one file to upload.")
    tables, problems = _read_files(job, files)
    if not tables:
        raise RuleViolation("; ".join(problems))
    job.status = "needs_mapping"
    job.error = ""
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    job_log(job, f"{user.username} added {len(tables)} table(s): {_describe(tables)}")
    for problem in problems:
        job_log(job, problem, "error")
    store_tables(job, tables)
    return job


# --------------------------------------------------------------------------- browser agent

def start_browser_job(connector: Connector, user) -> IngestionJob:
    require_role(user, "maker", "read the HR tool")
    if not connector.recipe.get("datasets"):
        raise RuleViolation("Find the HR tool's screens first (step 1), and confirm them.")
    if not connector.recipe_confirmed_at:
        raise RuleViolation("A person must confirm which screens to read before the agent reads them.")
    job = IngestionJob.objects.create(company=connector.company, connector=connector, kind="browser",
                                      source_system=connector.system_name, status="queued", started_by=user)
    thread = threading.Thread(target=_run_browser_job, args=(job.pk,), name=f"agent-job-{job.pk}", daemon=True)
    _running[job.pk] = thread
    transaction.on_commit(thread.start)
    return job


def _run_browser_job(job_id: int):
    from .browser_agent import AgentError, BrowserAgent

    close_old_connections()
    job = IngestionJob.objects.select_related("connector", "company").get(pk=job_id)
    job.status = "running"
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        agent = BrowserAgent(job.connector, lambda msg, level="info", **kw: job_log(job, msg, level, **kw), job=job)
        tables, checks = agent.read_all()
        checks += fewer_rows_than_before(job, tables)
        job.stats ={**(job.stats or {}), "checks": checks, "pages": agent.pages_visited, "relogins": agent.relogins}
        job.finished_at = timezone.now()
        job.status = "needs_mapping"
        job.save()
        store_tables(job, tables)
    except AgentError as exc:
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save()
        job_log(job, str(exc), "error")
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the user
        log.exception("agent job %s crashed", job_id)
        job.status = "failed"
        job.error = f"Unexpected error: {exc}"
        job.finished_at = timezone.now()
        job.save()
    finally:
        _running.pop(job_id, None)
        close_old_connections()


def fewer_rows_than_before(job: IngestionJob, tables: list[dict]) -> list[dict]:
    """A screen that suddenly shows far fewer rows than the last read of the same connection usually means the
    screen changed (a filter, a new layout), not that half the company left. Say so before anyone relies on it."""
    previous = (IngestionJob.objects.filter(connector=job.connector, kind="browser", id__lt=job.pk)
                .exclude(status__in=["failed", "queued", "running"]).order_by("-id").first())
    if previous is None:
        return []
    before = {t.dataset_hint: len(t.rows) for t in previous.tables.all()}
    checks = []
    for t in tables:
        old, new = before.get(t["dataset_hint"], 0), len(t["rows"])
        if old >= 4 and new < old / 2:
            checks.append({"level": "warning", "code": "fewer_rows",
                           "message": f"{t['name']}: {new} rows read, the last read (#{previous.pk}) had {old}. If nobody left, the "
                                      "screen may have changed: open it in the HR tool, or find the screens again."})
    return checks


def mark_if_interrupted(job: IngestionJob) -> IngestionJob:
    """A read that was running when the server stopped cannot resume; say so instead of spinning forever."""
    if job.status in ("running", "queued") and job.pk not in _running:
        beat = (job.stats or {}).get("heartbeat")
        last = timezone.datetime.fromisoformat(beat) if beat else job.created_at
        if timezone.now() - last > timedelta(minutes=2):
            job.status = "failed"
            job.error = "The read was interrupted (the server restarted). Start it again; pages already confirmed are reused."
            job.finished_at = timezone.now()
            job.save()
    return job
