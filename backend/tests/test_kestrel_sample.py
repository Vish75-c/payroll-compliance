"""The pay-cycle sample company (data/kestrel), brought in the way the onboarding wizard does it, gives the November
the sample's README promises, with its two pay groups and with one cycle for everyone: the files (Horilla's half-day
columns included) are mapped, read and calculated as expected, and TDS is due after each group's pay date."""
import json
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from payroll.demo import setup
from payroll.models import Company

from .conftest import REPO

pytestmark = pytest.mark.django_db
JSON = "application/json"
FOLDER = REPO / "data/kestrel"


def onboard(cycle: dict, groups: list[dict]) -> Company:
    setup.ensure_users()
    setup.load_statutory_rules()
    c = json.loads((FOLDER / "seed.json").read_text())["company"]
    maker = Client()
    assert maker.post("/api/login", {"username": "maker", "password": "maker123"}, content_type=JSON).status_code == 200
    created = maker.post("/api/companies", {k: c[k] for k in ("name", "state", "city", "pan", "tan", "pf_establishment_code",
                                                               "esi_employer_code", "pay_day")}, content_type=JSON)
    assert created.status_code == 201, created.content
    cid = created.json()["id"]
    for key, payload in c["settings"].items():
        r = maker.post(f"/api/companies/{cid}/policies", {"key": key, "effective_from": "2026-04-01", "payload": payload}, content_type=JSON)
        assert r.status_code == 201, r.content
    default = next(g for g in maker.get(f"/api/companies/{cid}/pay-groups").json()["groups"] if g["default"])
    r = maker.post(f"/api/pay-groups/{default['id']}/cycles", cycle, content_type=JSON)
    assert r.status_code == 201, r.content
    for group in groups:
        r = maker.post(f"/api/companies/{cid}/pay-groups", group, content_type=JSON)
        assert r.status_code == 201, r.content
    files = {key: SimpleUploadedFile(name, (FOLDER / name).read_bytes())
             for key, name in (("employees", "employees.xlsx"), ("compensation", "salaries.xlsx"), ("leave", "leave.csv"))}
    job = maker.post(f"/api/companies/{cid}/uploads", files).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"],
                                                                     "value_maps": t["mapping"]["value_maps"]}, content_type=JSON)
            assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [x for x in snap["checks"] if x["level"] == "error"], snap["checks"]
    assert maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type=JSON).status_code == 200
    ob = maker.post(f"/api/companies/{cid}/opening-balances", {"period": c["first_month"], "file": SimpleUploadedFile(
        "opening_balances.csv", (FOLDER / "opening_balances.csv").read_bytes())})
    assert ob.status_code == 200, ob.content
    return Company.objects.get(pk=cid)


@pytest.mark.parametrize("name", ["groups", "shifted", "attendance_cutoff", "calendar"])
def test_kestrel_november_matches_the_readme(name):
    expected = json.loads((FOLDER / "expected.json").read_text())
    seed = json.loads((FOLDER / "seed.json").read_text())["company"]
    cycle = {"groups": {"mode": "calendar", "start_day": 1}, "shifted": {"mode": "shifted", "start_day": 26},
             "attendance_cutoff": {"mode": "attendance_cutoff", "start_day": 26}, "calendar": {"mode": "calendar", "start_day": 1}}[name]
    company = onboard(cycle, seed["pay_groups"] if name == "groups" else [])
    run = setup.run_month(company, expected["period"], log=lambda *a: None)
    got = {e["code"]: e for e in run.result["employees"]}
    want = {e["code"]: e for e in expected["variants"][name]["employees"]}
    assert set(got) == set(want)
    problems = [f"{code} {key}: app {got[code]['amounts'].get(key, '0')}, expected {value}"
                for code, row in want.items() for key, value in row.items()
                if key not in ("code", "name", "case", "pay_group", "paid_for", "unpaid_counted", "pay_date")
                and got[code]["amounts"].get(key, "0") != value]
    problems += [f"{code} pay group: app {got[code]['pay_window']['group_name']}, expected {row['pay_group']}"
                 for code, row in want.items() if got[code]["pay_window"]["group_name"] != row["pay_group"]]
    assert not problems, "\n".join(problems)
    if name == "groups":  # KS005's group comes from the HR file's own "Pay Group" column, the others from the groups' rules
        assert {c: got[c]["pay_window"]["group_source"] for c in ("KS001", "KS002", "KS005")} == {"KS001": "rule", "KS002": "rule", "KS005": "hr_data"}
    # TDS is due after the month each group is paid in; PF by the 15th of the next month
    dues = expected["variants"][name]["dues"]
    tds = {x.jurisdiction: (x.total, x.due_date.isoformat()) for x in run.dues.filter(kind="tds")}
    assert tds == {m: (Decimal(x["total"]), x["due"]) for m, x in dues["tds"].items()}
    assert run.dues.get(kind="pf").due_date == date.fromisoformat(dues["pf"])
