"""What the system takes in equals the HR data, field by field, for every person, whatever the route.

The expected facts come straight from data/brickline/seed.json (the data every route was made from), worked
out here in plain code. Routes checked: the single workbook, the same sheets uploaded as three files, the
separate files kept in data/brickline/separate_files, Horilla's own exports, and the Demo HRMS screens read by
the browser agent. Horilla's live screens are checked too when Horilla is running (HORILLA_URL).
"""
import json
import os
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from payroll.models import DataSnapshot

from .conftest import REPO
from .test_api import _sheet_file, login

pytestmark = pytest.mark.django_db
SEED = json.loads((REPO / "data/brickline/seed.json").read_text())
STATE = {"Karnataka": "KA", "Maharashtra": "MH"}


HORILLA_STAGE_END = {"june": "2025-06-30", "july": "2025-07-31", "august": "2025-08-31", "september": "2025-09-30",
                     "october": "2025-10-31"}


def seed_as_of(day: str) -> dict:
    """What HR had entered in Horilla by the end of a stage (see integrations/horilla/seed_brickline.py): people who
    had joined (everyone joining by July is there from the start), salaries recorded by then, leaving dates that
    had passed, and leave."""
    known_until = max(day, HORILLA_STAGE_END["july"])
    people = [{k: v for k, v in e.items() if k != "doe" or v <= day} for e in SEED["employees"] if e["doj"] <= known_until]
    codes = {e["code"] for e in people}
    return {"employees": people, "salaries": [r for r in SEED["salaries"] if r["code"] in codes and r["recorded_on"] <= day],
            "leave": [lv for lv in SEED["leave"] if lv["code"] in codes and lv["from"] <= known_until], "split_50_20_30": True}


def expected(seed=SEED):
    people = {e["code"]: {"name": e["name"], "gender": e["gender"], "date_of_joining": e["doj"], "date_of_exit": e.get("doe"),
                          "work_state": STATE[e["location"].split(",")[-1].strip()]} for e in seed["employees"]}
    salary = {}
    for s in seed["salaries"]:
        gross = s["basic"] + s["hra"] + s["special"]
        if seed is SEED or seed.get("split_50_20_30"):
            assert s["basic"] * 2 == gross and s["hra"] * 5 == gross  # 50 / 20 / 30, so a gross-only source splits back exactly
        salary[(s["code"], s["effective_from"])] = {"components": {"basic": Decimal(s["basic"]), "hra": Decimal(s["hra"]),
                                                                   "special_allowance": Decimal(s["special"])},
                                                    "recorded_on": s["recorded_on"]}
    # Only approved leave is leave; it is unpaid only when it is Loss of Pay.
    leave = {(lv["code"], lv["from"], lv["to"]): {"days": Decimal(str(lv["days"])), "unpaid": lv["type"] == "Loss of Pay"}
             for lv in seed["leave"] if lv["status"] == "Approved"}
    return people, salary, leave


def check(data: dict, *, recorded_dates=True, seed=SEED) -> list[str]:
    people, salary, leave = expected(seed)
    problems = []
    got = {e["code"]: e for e in data["employees"]}
    if set(got) != set(people):
        problems.append(f"employees: missing {sorted(set(people) - set(got))}, extra {sorted(set(got) - set(people))}")
    for code, want in people.items():
        for key, value in want.items():
            if code in got and got[code].get(key) != value:
                problems.append(f"{code} {key}: imported {got[code].get(key)!r}, HR data {value!r}")
    comp = {(r["employee_code"], r["effective_from"]): r for r in data["compensation"]}
    if set(comp) != set(salary):
        problems.append(f"salary rows: missing {sorted(set(salary) - set(comp))[:5]}, extra {sorted(set(comp) - set(salary))[:5]}")
    for key, want in salary.items():
        if key not in comp:
            continue
        components = {c: Decimal(v) for c, v in comp[key]["components"].items()}
        if components != want["components"]:
            problems.append(f"{key} salary: imported {components}, HR data {want['components']}")
        if recorded_dates and comp[key].get("recorded_on") != want["recorded_on"]:
            problems.append(f"{key} recorded on: imported {comp[key].get('recorded_on')}, HR data {want['recorded_on']}")
    lv = {(r["employee_code"], r["start_date"], r["end_date"]): r for r in data["leave"]}
    if set(lv) != set(leave):
        problems.append(f"leave: missing {sorted(set(leave) - set(lv))[:5]}, extra {sorted(set(lv) - set(leave))[:5]}")
    for key, want in leave.items():
        if key in lv and (Decimal(lv[key]["days"]) != want["days"] or bool(lv[key]["unpaid"]) != want["unpaid"]):
            problems.append(f"leave {key}: imported {lv[key]['days']} days unpaid={lv[key]['unpaid']}, HR data {want}")
    return problems


def _upload(maker, company, files: dict) -> dict:
    job = maker.post(f"/api/companies/{company.pk}/uploads", files).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"],
                                                                     "value_maps": t["mapping"]["value_maps"]},
                           content_type="application/json")
            assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [c for c in snap["checks"] if c["level"] == "error"], snap["checks"]
    return DataSnapshot.objects.get(pk=snap["id"]).data


def test_the_workbook_is_imported_exactly(demo):
    _, snapshot = demo
    assert check(snapshot.data) == []


def test_the_check_notices_a_single_wrong_fact(demo):
    import copy

    _, snapshot = demo
    for change in (lambda d: d["employees"][5].update(work_state="MH" if d["employees"][5]["work_state"] != "MH" else "KA"),
                   lambda d: d["compensation"][3]["components"].update(basic="1"),
                   lambda d: d["leave"][0].update(unpaid=not d["leave"][0]["unpaid"]),
                   lambda d: d["employees"].pop()):
        data = copy.deepcopy(snapshot.data)
        change(data)
        assert len(check(data)) == 1


def test_the_same_sheets_as_three_files_are_imported_exactly(demo):
    company, _ = demo
    data = _upload(login("maker"), company, {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary"),
                                             "leave": _sheet_file("Leave", as_csv=True)})
    assert check(data) == []


def test_the_separate_files_are_imported_exactly(demo):
    company, _ = demo
    folder = REPO / "data/brickline/separate_files"
    files = {key: SimpleUploadedFile(name, (folder / name).read_bytes()) for key, name in
             (("employees", "employee_master.xlsx"), ("compensation", "salary_contracts.xlsx"), ("leave", "leave_july.csv"))}
    assert check(_upload(login("maker"), company, files)) == []


def test_horilla_exports_are_imported_exactly(demo):
    company, _ = demo
    folder = REPO / "integrations/horilla/fixtures"
    files = {key: SimpleUploadedFile(name, (folder / name).read_bytes()) for key, name in
             (("employees", "employee_export.xlsx"), ("compensation", "Contract_export.xlsx"), ("leave", "Leave_requests.xlsx"))}
    data = _upload(login("maker"), company, files)
    # The sample exports were taken when Horilla had July's data. Horilla keeps no "recorded on" date for a
    # contract; the contract start is assumed and flagged as assumed.
    assert check(data, recorded_dates=False, seed=seed_as_of(HORILLA_STAGE_END["july"])) == []
    assert all(r["recorded_on_assumed"] for r in data["compensation"])


def test_the_onboarding_sample_company_is_imported_exactly(northwind):
    from payroll.models import OpeningBalance

    company, seed, snapshot_id = northwind
    assert check(DataSnapshot.objects.get(pk=snapshot_id).data, seed=seed) == []
    import csv

    with open(REPO / "data/northwind/opening_balances.csv") as f:
        rows = {r["Employee ID"]: (Decimal(r["Gross paid this year"]), Decimal(r["TDS this year"])) for r in csv.DictReader(f)}
    stored = {b.employee_code: (b.gross, b.tds) for b in OpeningBalance.objects.filter(company=company)}
    assert stored == rows and len(rows) == 23


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_demo_hrms_screens_read_by_the_agent_are_imported_exactly(hrms):
    from django.utils import timezone

    from payroll.features.hr_data import jobs
    from payroll.models import Connector
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots

    from .test_agent import _read

    url, _ = hrms
    setup.ensure_users()
    setup.load_statutory_rules()
    company = setup.ensure_company("fid", "Fidelity Co", "KA")
    maker = setup.system_user("maker")
    conn = Connector.objects.create(company=company, name="Demo HRMS", kind="browser", system_name="Demo HRMS", base_url=url,
                                    username="hr.admin", password="demo123", recipe_confirmed_at=timezone.now(),
                                    recipe={"datasets": {"employees": {"url": "/people"}, "compensation": {"url": "/compensation"},
                                                         "leave": {"url": "/time-off"}}})
    job = _read(conn, maker)
    jobs.suggest(job, maker)
    for t in job.tables.all():
        t.refresh_from_db()
        jobs.confirm(t, maker, t.mapping.mapping, t.mapping.value_maps)
    job.refresh_from_db()
    snap = snapshots.create_from_job(job, maker)
    assert check(snap.data) == []


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_horilla_live_screens_are_imported_exactly():
    """Needs Horilla running with the Brickline data (scripts/horilla.sh); skipped otherwise."""
    import urllib.request

    base = os.environ.get("HORILLA_URL", "http://localhost:8000/")
    try:
        urllib.request.urlopen(base, timeout=2)
    except OSError:
        pytest.skip(f"Horilla is not running at {base}")
    from django.utils import timezone

    from payroll.features.hr_data import jobs, profiles
    from payroll.models import Connector
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots

    from .test_agent import _read

    setup.ensure_users()
    setup.load_statutory_rules()
    company = setup.ensure_company("fid-h", "Fidelity Horilla", "KA")
    maker = setup.system_user("maker")
    horilla = profiles.profile_for("horilla")
    conn = Connector.objects.create(company=company, name="Horilla", kind="browser", system_name=horilla.name, profile=horilla.system,
                                    base_url=base, username=os.environ.get("HORILLA_USER", "admin"),
                                    password=os.environ.get("HORILLA_PASSWORD", "admin"), recipe_confirmed_at=timezone.now(),
                                    recipe={"datasets": profiles.screens_for(horilla)})
    job = _read(conn, maker)
    assert job.status in ("needs_mapping", "ready"), job.error
    for t in job.tables.all():
        if not (t.mapping and t.mapping.confirmed_at):
            jobs.suggest(job, maker)
            t.refresh_from_db()
            jobs.confirm(t, maker, t.mapping.mapping, t.mapping.value_maps)
    job.refresh_from_db()
    snap = snapshots.create_from_job(job, maker)
    stage_file = REPO / "var/horilla/stage"
    stage = stage_file.read_text().strip() if stage_file.exists() else "july"
    seed = seed_as_of(HORILLA_STAGE_END[stage])
    people_file = REPO / "var/horilla/people"  # set when Horilla holds only some of the people (BRICKLINE_KEEP)
    keep = set(filter(None, people_file.read_text().strip().split(","))) if people_file.exists() else set()
    if keep:
        seed = {**seed, **{k: [r for r in seed[k] if r["code"] in keep] for k in ("employees", "salaries", "leave")}}
    assert check(snap.data, recorded_dates=False, seed=seed) == []

