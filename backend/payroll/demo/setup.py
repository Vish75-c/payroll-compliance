"""Setup helpers shared by the seed command, the history command and the answer-sheet tests."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from payroll_engine.periods import Period

from ..features.hr_data import jobs as ingestion_jobs
from ..features.hr_data import profiles
from ..features.hr_data import snapshots as snapshot_service
from ..features.hr_data.canonical import heuristic_mapping
from ..features.payroll_runs import changes as change_service
from ..features.payroll_runs import runs as run_service
from ..models import Company, Connector, PayrollRun, PolicyVersion

REPO = Path(settings.REPO_DIR)
DEMO_USERS = [
    # username, password, first name, last name, groups
    ("maker", "maker123", "Meena", "Iyer (Finance team)", ["maker"]),
    ("checker", "checker123", "Rahul", "Nair (Finance head)", ["checker"]),
    ("admin", "admin123", "Admin", "", ["maker", "checker"]),
]


def ensure_users(create_demo=True):
    for name in ("maker", "checker"):
        Group.objects.get_or_create(name=name)
    if not create_demo:
        return
    User = get_user_model()
    for username, password, first, last, groups in DEMO_USERS:
        user, created = User.objects.get_or_create(username=username, defaults={"first_name": first, "last_name": last})
        if created:
            user.set_password(password)
            if username == "admin":
                user.is_staff = user.is_superuser = True
            user.save()
        elif (user.first_name, user.last_name) != (first, last):  # demo accounts keep their demo names
            user.first_name, user.last_name = first, last
            user.save(update_fields=["first_name", "last_name"])
        for g in groups:
            user.groups.add(Group.objects.get(name=g))


def system_user(role: str):
    """Service accounts used by command-line setup steps (never by the web app)."""
    User = get_user_model()
    user, created = User.objects.get_or_create(username=f"system-{role}", defaults={"first_name": "System", "last_name": role})
    if created:
        user.set_unusable_password()
        user.save()
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


def load_statutory_rules(path: Path | None = None) -> int:
    path = path or REPO / "data" / "rules" / "statutory_in.json"
    created = 0
    for v in json.loads(Path(path).read_text())["versions"]:
        exists = PolicyVersion.objects.filter(company__isnull=True, key=v["key"], jurisdiction=v["jurisdiction"],
                                              effective_from=v["effective_from"]).exists()
        if exists:
            continue
        PolicyVersion.objects.create(company=None, scope=PolicyVersion.SCOPE_STATUTORY, key=v["key"], jurisdiction=v["jurisdiction"],
                                     effective_from=v["effective_from"], payload=v["payload"], title=v.get("title", ""), note=v.get("note", ""))
        created += 1
    return created


def ensure_company(code: str, name: str, state: str, city: str = "") -> Company:
    company, _ = Company.objects.get_or_create(code=code, defaults={"name": name, "state": state, "city": city})
    return company


def set_company_settings(company: Company, values: dict, effective_from: str, note: str = "") -> int:
    """Add a setting version unless the newest version on that date already holds the same values."""
    created = 0
    for key, payload in values.items():
        latest = PolicyVersion.objects.filter(company=company, key=key, effective_from=effective_from).order_by("-id").first()
        if latest and latest.payload == payload:
            continue
        from ..features.rules.policies import add_version

        add_version(company=company, key=key, jurisdiction="", effective_from=date.fromisoformat(effective_from), payload=payload,
                    title="", note=note, user=None)
        created += 1
    return created


def ensure_demo_connectors(company: Company):
    """The Horilla connection (Horilla runs separately; its address can be set with environment variables).

    Horilla is a known HR tool, so its screens come from integrations/horilla/profile.json (suggested only: a
    person confirms them). The bundled Demo HRMS (mock_hrms/app.py) is not added here; connect it with
    "+ Connect another HR tool" (http://localhost:8100/, hr.admin / demo123) to try an unknown HR tool."""
    horilla = profiles.profile_for("horilla")  # None when profiles are switched off (HR_TOOL_PROFILES=0)
    Connector.objects.get_or_create(
        company=company, name="Horilla", defaults={
            "kind": Connector.KIND_BROWSER, "system_name": "Horilla", "profile": horilla.system if horilla else "",
            "base_url": os.environ.get("HORILLA_URL", "http://localhost:8000/"),
            "username": os.environ.get("HORILLA_USER", "admin"), "password": os.environ.get("HORILLA_PASSWORD", "admin"),
            "recipe": {"datasets": profiles.screens_for(horilla)} if horilla else {},
        })


@transaction.atomic
def import_spreadsheet(company: Company, path: Path, mapping_file: Path | None = None, log=print):
    """Read a spreadsheet and confirm its mapping without AI (saved mapping, given file, or rule-based)."""
    maker = system_user("maker")
    job = ingestion_jobs.run_spreadsheet_job(company, maker, [(Path(path).name, Path(path).read_bytes(), "")])
    if job.status == "failed":
        raise RuntimeError(job.error)
    given = json.loads(Path(mapping_file).read_text()) if mapping_file else {}
    for table in job.tables.all():
        if table.mapping and table.mapping.confirmed_at:
            continue
        mapping = given.get(table.name)
        if mapping is None:
            guess = heuristic_mapping(table.headers, table.dataset_hint, default_year=None)
            mapping = {h: g["target"] for h, g in guess.items()}
            log(f"  {table.name}: rule-based mapping " + ", ".join(f"{h} -> {t}" for h, t in mapping.items()))
        ingestion_jobs.confirm(table, maker, mapping)
    job.refresh_from_db()
    snapshot = snapshot_service.create_from_job(job, maker)
    errors = [c for c in snapshot.checks if c["level"] == "error"]
    if errors:
        raise RuntimeError("The spreadsheet has errors: " + "; ".join(c["message"] for c in errors))
    snapshot_service.approve(snapshot, maker, note="imported from the command line")
    return snapshot


def run_month(company: Company, period: str, instructions: list[str] | None = None, finalize: bool = True,
              knowledge_date: date | None = None, log=print, day_count: int | None = None) -> PayrollRun:
    """Prepare, calculate and (optionally) approve a month with two different service accounts (maker, checker)."""
    maker, checker = system_user("maker"), system_user("checker")
    p = Period.parse(period)
    run = run_service.open_month(company, period, maker, knowledge_date=knowledge_date or p.end,
                                 note="processed from the command line", day_count=day_count)
    run_service.review_data(run.pk, maker)
    for text in instructions or []:
        change = change_service.propose(run, text, maker)
        if change.proposal.get("problems"):
            raise RuntimeError(f"Instruction not understood: {text!r}: {change.proposal['problems']}")
        change_service.confirm(change, maker)
        log(f"  {period}: confirmed '{text}' -> " + " ".join(i["summary"] for i in change.proposal["items"]))
    run = run_service.calculate(run.pk, maker)
    if finalize:
        run_service.submit(run.pk, maker)
        run_service.approve(run.pk, checker, "approved from the command line")
        run = run_service.finalize(run.pk, checker)
    run.workflow_backend = "local"
    run.save(update_fields=["workflow_backend"])
    return run



def record_past_payments(run: PayrollRun, log=print) -> int:
    """For months processed as already paid: record their government dues as paid on time (two days before the
    due date), with references marked DEMO so they are never mistaken for real challans."""
    from datetime import timedelta

    from ..features.dues import dues as dues_service

    checker = system_user("checker")
    done = 0
    for due in run.dues.filter(paid_on__isnull=True):
        when = max(due.due_date - timedelta(days=2), run.period_obj.end)
        ref = f"DEMO-{due.kind.upper()}{('-' + due.jurisdiction) if due.jurisdiction else ''}-{run.period.replace('-', '')}"
        dues_service.mark_paid(due.pk, checker, when.isoformat(), ref, "Recorded by the demo setup for a month paid before.")
        done += 1
    return done
