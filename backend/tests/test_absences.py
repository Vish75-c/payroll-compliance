"""Attendance: days present are read exactly, and working days with no check-in and no approved leave are suggested as
possible unpaid absences, which a maker adds or dismisses. The expected answers come from data/brickline/seed.json
("attendance_gaps": who really did not check in), not from the code under test."""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from payroll.models import DataSnapshot, PayrollRun, RunEvent

from .conftest import REPO
from .test_api import _sheet_file, login
from .test_import_fidelity import SEED

pytestmark = pytest.mark.django_db
JSON = "application/json"
FOLDER = REPO / "data/brickline/attendance"


def _attendance(*months, cut_after=None):
    files = []
    for month in months:
        text = (FOLDER / f"attendance_{month}.csv").read_text()
        if cut_after:
            text = "\n".join(line for line in text.splitlines() if line.startswith("Employee") or line.split(",")[1] <= cut_after) + "\n"
        files.append(SimpleUploadedFile(f"attendance_{month}.csv", text.encode()))
    return files


def _read(maker, company, attendance) -> int:
    files = {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary"), "leave": _sheet_file("Leave")}
    job = maker.post(f"/api/companies/{company.pk}/uploads", {**files, "attendance": attendance}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"], "value_maps": t["mapping"]["value_maps"]},
                           content_type=JSON)
            assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [c for c in snap["checks"] if c["level"] == "error"], snap["checks"]
    assert maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type=JSON).status_code == 200
    return snap["id"]


def _open(maker, company, period) -> int:
    run = maker.post(f"/api/companies/{company.pk}/runs", {"period": period}, content_type=JSON).json()
    maker.post(f"/api/runs/{run['id']}/review-data", content_type=JSON)
    return run["id"]


def _suggested(maker, rid):
    out = maker.get(f"/api/runs/{rid}/possible-absences").json()
    return out, {(a["employee_code"], tuple(a["dates"])) for a in out["absences"]}


def _gaps(month):
    """The seed's gaps of a month, each person's days grouped the way a person reads them (12-13 Aug is one)."""
    by_person = {}
    for g in SEED["attendance_gaps"]:
        if g["date"].startswith(month):
            by_person.setdefault(g["code"], []).append(g["date"])
    return by_person


def test_days_present_are_read_exactly():
    """Independent check of the generated files: present on every working day of employment, except approved leave,
    the gaps, and a month without an attendance card."""
    holidays = {d for _, d in SEED["holidays"]}
    gaps = {(g["code"], g["date"]) for g in SEED["attendance_gaps"]}
    no_card = {(x["code"], x["month"]) for x in SEED["no_attendance"]}
    leave = {(lv["code"], (date.fromisoformat(lv["from"]) + timedelta(k)).isoformat()) for lv in SEED["leave"] if lv["status"] == "Approved"
             for k in range((date.fromisoformat(lv["to"]) - date.fromisoformat(lv["from"])).days + 1)}
    import csv

    for month in ("2025-08", "2025-09", "2025-10"):
        y, m = map(int, month.split("-"))
        want = set()
        for e in SEED["employees"]:
            for n in range(1, calendar.monthrange(y, m)[1] + 1):
                day = date(y, m, n).isoformat()
                if (date(y, m, n).weekday() < 5 and day not in holidays and e["doj"] <= day and (not e.get("doe") or day <= e["doe"])
                        and (e["code"], day) not in leave and (e["code"], day) not in gaps and (e["code"], month) not in no_card):
                    want.add((e["code"], day))
        with open(FOLDER / f"attendance_{month}.csv") as f:
            got = {(r["Employee ID"], r["Date"]) for r in csv.DictReader(f)}
        assert got == want, month


def test_the_suggestions_are_the_days_nobody_reported(demo):
    company, _ = demo
    maker = login("maker")
    snap = _read(maker, company, _attendance("2025-08", "2025-09", "2025-10"))
    data = DataSnapshot.objects.get(pk=snap).data
    assert data["presence_range"] == {"from": "2025-08-01", "to": "2025-10-31"}
    prev = None
    for month in ("2025-08", "2025-09", "2025-10"):
        if month != "2025-08":  # each month needs HR data read after the month before it was opened
            _read(maker, company, _attendance("2025-08", "2025-09", "2025-10"))
        if prev:  # a month opens only once the month before is final; this test is about suggestions, not sign-off
            PayrollRun.objects.filter(pk=prev).update(status=PayrollRun.FINALIZED)
        prev = _open(maker, company, month)
        out, got = _suggested(maker, prev)
        assert out["status"] == "ok" and out["checked"]["closed"] == []
        gaps = _gaps(month)
        # one suggestion per run of working days (BL066's Friday 26 and Monday 29 September are one)
        assert {code for code, _ in got} == set(gaps)
        assert {(code, d) for code, dates in got for d in dates} == {(code, d) for code, days in gaps.items() for d in days}
        no_card = {x["code"] for x in SEED["no_attendance"] if x["month"] == month}
        assert {p["employee_code"] for p in out["no_check_in"]} == no_card
    assert ("BL066", ("2025-09-26", "2025-09-29")) in _suggested(maker, PayrollRun.objects.get(company=company, period="2025-09").pk)[1]
    # Leaving and joining: Gaurav Dutta (left 14 Aug, one unpaid leave day) and Kabir Sethi (joined 4 Aug) are not suggested.
    aug = _suggested(maker, PayrollRun.objects.get(company=company, period="2025-08").pk)[1]
    assert not {code for code, _ in aug} & {"BL033", "BL081"}


def test_adding_and_dismissing_a_suggestion(demo):
    company, _ = demo
    maker, checker = login("maker"), login("checker")
    _read(maker, company, _attendance("2025-08"))
    rid = _open(maker, company, "2025-08")
    out, got = _suggested(maker, rid)
    absent = next(a for a in out["absences"] if a["employee_code"] == "BL016")
    forgot = next(a for a in out["absences"] if a["employee_code"] == "BL057")
    assert absent["label"] == "12–13 Aug" and absent["days"] == 2

    added = maker.post(f"/api/runs/{rid}/add-by-hand", {"kind": "extra_unpaid_days", "employee_code": "BL016", "dates": absent["dates"],
                                                        "reason": absent["suggested_reason"]}, content_type=JSON).json()
    item = added["proposal"]["items"][0]
    assert item["dates"] == ["2025-08-12", "2025-08-13"] and item["unpaid_days"] == "2" and "(12 and 13 Aug 2025)" in item["summary"]
    assert {code for code, _ in _suggested(maker, rid)[1]} == {"BL057"}

    assert maker.post(f"/api/runs/{rid}/possible-absences/dismiss", {"employee_code": "BL057", "dates": forgot["dates"], "reason": " "},
                      content_type=JSON).status_code == 400
    assert checker.post(f"/api/runs/{rid}/possible-absences/dismiss", {"employee_code": "BL057", "dates": forgot["dates"], "reason": "x"},
                        content_type=JSON).status_code == 403
    left = maker.post(f"/api/runs/{rid}/possible-absences/dismiss", {"employee_code": "BL057", "dates": forgot["dates"],
                                                                      "reason": "Forgot to punch; was at the client's site"}, content_type=JSON).json()
    assert left["absences"] == []
    assert "Forgot to punch" in RunEvent.objects.get(run_id=rid, action="absence_dismissed").message

    # The two days are cut like any unpaid day: monthly rate ÷ 31 × 2 for each part of pay.
    maker.post(f"/api/runs/{rid}/calculate", content_type=JSON)
    akash = next(e for e in maker.get(f"/api/runs/{rid}").json()["result"]["employees"] if e["code"] == "BL016")["amounts"]
    rates = [Decimal(v) for k, v in akash.items() if k.startswith("rate.")]
    assert akash["unpaid_days"] == "2"
    assert Decimal(akash["lop_deduction"]) == sum((r * 2 / 31).quantize(Decimal(1), rounding=ROUND_HALF_UP) for r in rates)

    maker.post(f"/api/runs/{rid}/submit", content_type=JSON)
    refused = maker.post(f"/api/runs/{rid}/possible-absences/dismiss", {"employee_code": "BL016", "dates": ["2025-08-14"], "reason": "x"},
                         content_type=JSON)
    assert refused.status_code == 400 and "cannot change" in refused.json()["detail"]


def test_safeguards(demo):
    company, _ = demo
    maker = login("maker")
    # No attendance in the data in use: nothing is suggested, and the page says why.
    rid = _open(maker, company, "2025-08")
    assert _suggested(maker, rid)[0]["status"] == "no_attendance"
    maker.post(f"/api/runs/{rid}/cancel", content_type=JSON)

    # Attendance up to 14 August only: only those days are checked.
    _read(maker, company, _attendance("2025-08", cut_after="2025-08-14"))
    rid = _open(maker, company, "2025-08")
    out, got = _suggested(maker, rid)
    assert out["checked"]["to"] == "2025-08-14" and "covers 01 Aug 2025 to 14 Aug 2025 only" in out["message"]
    assert {code for code, _ in got} == {"BL016"}
    maker.post(f"/api/runs/{rid}/cancel", content_type=JSON)

    # No holidays on the calendar: the days almost nobody checked in are taken as holidays, not as everyone absent.
    r = maker.post(f"/api/companies/{company.pk}/policies", {"key": "work_calendar", "effective_from": "2025-04-01",
                                                             "payload": {"weekly_off": ["saturday", "sunday"], "holidays": []}}, content_type=JSON)
    assert r.status_code == 201
    _read(maker, company, _attendance("2025-08"))
    rid = _open(maker, company, "2025-08")
    out, got = _suggested(maker, rid)
    assert out["checked"]["closed"] == ["2025-08-15", "2025-08-27"] and "Almost nobody checked in on 15 and 27 Aug" in out["message"]
    assert {code for code, _ in got} == {"BL016", "BL057"}


def test_horilla_attendance_export_is_read_like_the_file(demo):
    """Horilla's own export of August maps from its profile (no AI) and gives the same days present as the CSV."""
    company, _ = demo
    maker = login("maker")
    a = DataSnapshot.objects.get(pk=_read(maker, company, _attendance("2025-08"))).data["presence"]
    export = SimpleUploadedFile("Attendance_export.xlsx", (REPO / "integrations/horilla/fixtures/Attendance_export_2025-08.xlsx").read_bytes())
    b = DataSnapshot.objects.get(pk=_read(maker, company, [export])).data["presence"]
    assert a == b and len(a) == 82


def test_a_file_ending_before_a_weekend_covers_the_month(demo):
    company, _ = demo
    maker = login("maker")
    _read(maker, company, _attendance("2025-08"))   # the last check-in is Friday 29 August; 30 and 31 are a weekend
    out, _ = _suggested(maker, _open(maker, company, "2025-08"))
    assert out["checked"]["to"] == "2025-08-29" and "only" not in out["message"]
