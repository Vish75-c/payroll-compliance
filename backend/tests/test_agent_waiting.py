"""The browser agent reading the Demo HRMS again for the people waiting for HR data, for real (Chromium, no stand-in):
two people whose details HR has not entered, a month paid for everyone else, HR filling in one of them, the agent's
read finding that one, and the other held and paid with the next month. Skipped if Chromium is not installed."""
import http.cookiejar
import urllib.parse
import urllib.request

import pytest
from django.utils import timezone

from payroll.demo import setup
from payroll.features.hr_data import jobs, snapshots, sync
from payroll.features.payroll_runs import employees, runs
from payroll.models import Connector, DataSnapshot, RunEmployee

from .test_agent import _read

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]
NO_LOCATION, NO_GENDER = "BL004", "BL012"  # Kavya Reddy, Deepika Khanna


def _hr_enters(url, code, field):
    """HR fills in a missing detail on the Demo HRMS (its /hr/missing page), signed in like a person."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.open(url + "login", urllib.parse.urlencode({"login_id": "hr.admin", "passcode": "demo123", "next": "/hr/missing"}).encode())
    page = opener.open(url + "hr/missing", urllib.parse.urlencode({"code": code, "field": field}).encode()).read().decode()
    assert "Saved:" in page


def _reviewed(job, maker):
    job.refresh_from_db()
    if job.status != "ready":  # the first read: a person confirms the field mapping once
        jobs.suggest(job, maker)
        for t in job.tables.all():
            t.refresh_from_db()
            jobs.confirm(t, maker, t.mapping.mapping, t.mapping.value_maps)
    snap = snapshots.create_from_job(job, maker)
    assert not snap.has_blocking_issues, [c for c in snap.checks if c["level"] == "error"]
    return snapshots.approve(snap, maker)


def test_the_agent_reads_again_for_the_people_waiting(hrms, monkeypatch):
    url, mod = hrms
    monkeypatch.setenv("MOCK_HRMS_SESSION_PAGES", "0")
    monkeypatch.setattr(mod, "MISSING", {NO_LOCATION: {"location"}, NO_GENDER: {"gender"}})
    setup.ensure_users()
    setup.load_statutory_rules()
    company = setup.ensure_company("t", "Test Co", "KA")
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    conn = Connector.objects.create(
        company=company, name="Demo HRMS", kind="browser", system_name="Demo HRMS", base_url=url, username="hr.admin",
        password="demo123", recipe_confirmed_at=timezone.now(),
        recipe={"datasets": {"employees": {"url": url + "people"}, "compensation": {"url": url + "compensation"},
                             "leave": {"url": url + "time-off"}}})

    # 1. The first read: the two blank details make those two wait (warnings), not stop the data.
    first = _reviewed(_read(conn, maker), maker)
    assert first.data["incomplete"] == {NO_LOCATION: ["work_state"], NO_GENDER: ["gender"]}
    april = runs.open_month(company, "2025-04", maker)
    waiting = {r.employee_code: r.missing for r in april.employee_states.filter(status=RunEmployee.WAITING)}
    assert waiting == {NO_LOCATION: ["work_state"], NO_GENDER: ["gender"]}

    # 2. HR enters Kavya's location in the HR tool. The agent reads again for both: one read, Kavya found.
    _hr_enters(url, NO_LOCATION, "location")
    asks = [{"run_id": april.pk, "code": c, "missing": m} for c, m in waiting.items()]
    out = sync.read_for_waiting(company.code, asks)
    assert out["status"] == "read", out
    assert out["found"] == [f"{april.pk}:{NO_LOCATION}"] and out["still_missing"] == [f"{april.pk}:{NO_GENDER}"]
    second = DataSnapshot.objects.get(pk=out["snapshot_id"])
    assert second.status == "pending_review" and second.data["incomplete"] == {NO_GENDER: ["gender"]}
    assert second.job.status == "done" and second.job.stats["for_waiting"] == sorted(waiting)  # reused the confirmed mapping

    # 3. The finance team employee reviews it and uses it: Kavya is paid in April; Deepika still waits, then is held.
    snapshots.approve(second, maker)
    runs.use_snapshot(april.pk, second, maker)
    runs.review_data(april.pk, maker)
    april = runs.calculate(april.pk, maker)
    paid = {e["code"] for e in april.result["employees"]}
    assert NO_LOCATION in paid and NO_GENDER not in paid
    employees.hold(april.pk, NO_GENDER, maker, "HR is checking her records")
    runs.submit(april.pk, maker)
    runs.approve(april.pk, checker)
    runs.finalize(april.pk, checker)
    assert NO_GENDER in runs.previous_month_block(company, april.period_obj.next())  # May waits for her data

    # 4. HR enters Deepika's gender; a read and a review later, May opens and pays her April salary.
    _hr_enters(url, NO_GENDER, "gender")
    assert sync.read_for_waiting(company.code, asks)["status"] == "nobody_waiting"  # April is final: nobody waits now
    third = _reviewed(_read(conn, maker), maker)
    assert "incomplete" not in third.data
    may = runs.open_month(company, "2025-05", maker, snapshot=third)
    runs.review_data(may.pk, maker)
    may = runs.calculate(may.pk, maker)
    deepika = next(e for e in may.result["employees"] if e["code"] == NO_GENDER)
    assert [a["period"] for a in deepika["facts"]["arrears_detail"] if a.get("held")] == ["2025-04"]
