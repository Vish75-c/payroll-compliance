"""Failure paths: Temporal down when a month is opened, a failed finalise, dates the AI did not get from the
instruction, and the secret key."""
import os
import stat
from datetime import date

import pytest
from django.test import Client

from payroll.demo import setup
from payroll.features.payroll_runs import changes, exports, runs
from payroll.features.payroll_runs.workflow import dispatch
from payroll.models import PayrollRun
from payroll.shared.permissions import RuleViolation

from .test_workflow_rules import history, prepare_july

pytestmark = pytest.mark.django_db

UNREACHABLE = "127.0.0.1:1"


def login(username):
    c = Client()
    assert c.post("/api/login", {"username": username, "password": f"{username}123"}, content_type="application/json").status_code == 200
    return c


# --------------------------------------------------------------------------- Temporal down

def test_month_is_not_opened_when_its_workflow_cannot_start(demo, settings):
    company, _ = demo
    settings.WORKFLOW_BACKEND = "temporal"
    settings.TEMPORAL_ADDRESS = UNREACHABLE
    maker = login("maker")
    res = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-04"}, content_type="application/json")
    assert res.status_code == 503 and res.json()["code"] == "workflow_unavailable"
    # Nothing half-made is left behind, so the month can be opened once Temporal is back.
    assert not PayrollRun.objects.filter(company=company, period="2025-04").exists()


def test_month_without_a_workflow_never_runs_in_process(demo, settings):
    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-04", maker)  # opened without a workflow (as if Temporal was down)
    runs.review_data(run.pk, maker)
    settings.WORKFLOW_BACKEND = "temporal"
    settings.TEMPORAL_ADDRESS = UNREACHABLE
    run.refresh_from_db()
    with pytest.raises(RuleViolation) as err:
        dispatch.send(run, "calculate", maker)
    assert err.value.code == "workflow_unavailable"
    run.refresh_from_db()
    assert run.calculation_count == 0 and run.status == PayrollRun.DRAFT  # not done locally behind Temporal's back


def test_month_without_a_workflow_gets_one_before_its_next_step(demo, settings, monkeypatch):
    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-04", maker)
    runs.review_data(run.pk, maker)
    settings.WORKFLOW_BACKEND = "temporal"
    calls = []

    def fake_temporal(coro_factory):
        calls.append(coro_factory.__qualname__)

    monkeypatch.setattr(dispatch, "_temporal", fake_temporal)
    run.refresh_from_db()
    outcome = dispatch.send(run, "calculate", maker)
    run.refresh_from_db()
    assert outcome["mode"] == "temporal"
    assert run.workflow_backend == "temporal" and run.workflow_id == dispatch.workflow_id(run)
    assert calls == ["start.<locals>.go", "send.<locals>.go"]  # started, then signalled


# --------------------------------------------------------------------------- finalise

def test_files_that_do_not_match_stop_finalising_and_can_be_finalised_again(demo, monkeypatch):
    company, _ = demo
    history(company)
    setup.import_spreadsheet(company, setup.REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    run = prepare_july(company, maker)
    runs.submit(run.pk, maker)
    runs.approve(run.pk, checker, "ok")

    real = exports.reconcile
    monkeypatch.setattr(exports, "reconcile", lambda files, result: [{"check": "TDS", "matches": False}])
    with pytest.raises(RuleViolation) as err:
        runs.finalize(run.pk, checker)
    assert err.value.code == "files_mismatch" and "TDS" in err.value.message
    run.refresh_from_db()
    assert run.status == PayrollRun.APPROVED and not run.files  # the approval stands, no files claimed

    with pytest.raises(RuleViolation):  # only a checker can finalise again
        dispatch.precheck("finalize", run, maker, "")
    monkeypatch.setattr(exports, "reconcile", real)
    dispatch.send(run, "finalize", checker)
    run.refresh_from_db()
    assert run.status == PayrollRun.FINALIZED and run.last_error == ""
    with pytest.raises(RuleViolation, match="only an approved month"):
        dispatch.precheck("finalize", run, checker, "")


# --------------------------------------------------------------------------- dates in instructions

@pytest.mark.parametrize("text, when, ok", [
    ("Raise Priya to ₹90,000 from 1 May 2025", date(2025, 5, 1), True),
    ("Raise Priya to ₹90,000 from May 1st", date(2025, 5, 1), True),
    ("Raise Priya to ₹90,000 from May", date(2025, 5, 1), True),
    ("Raise Priya to ₹90,000 from 2025-05-01", date(2025, 5, 1), True),
    ("Raise Priya to ₹90,000 from 15th of May", date(2025, 5, 15), True),
    ("Raise Priya to ₹90,000", date(2025, 5, 1), False),            # "90" is not a date
    ("Raise Priya to ₹90,000 from 1 May 2025", date(2025, 6, 1), False),
    ("Raise Priya to ₹90,000 from 1 May 2024", date(2025, 5, 1), False),
    ("Raise Priya to ₹90,000 from May", date(2025, 5, 15), False),
])
def test_start_date_must_be_written(text, when, ok):
    assert changes.date_written(text, when) is ok


def test_salary_change_with_a_date_the_ai_supplied_is_refused(demo):
    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-07", maker)

    def proposal(text):
        return changes.validate_proposal(
            {"changes": [{"type": "salary_revision", "employee_as_written": "Priya", "amount_as_written": "₹90,000",
                          "effective_from": "2025-05-01", "month": None}], "unclear": None}, text, run)

    refused = proposal("Raise Priya's salary to ₹90,000")
    assert not refused["items"] and "not written in the instruction" in refused["problems"][0]
    accepted = proposal("Raise Priya's salary to ₹90,000 from 1 May 2025")
    assert accepted["items"][0]["effective_from"] == "2025-05-01" and not accepted["problems"]


# --------------------------------------------------------------------------- settings

def test_secret_key_is_random_kept_and_private(tmp_path, monkeypatch):
    from config import settings as conf

    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    monkeypatch.setattr(conf, "DATA_DIR", tmp_path)
    first = conf._secret_key()
    assert len(first) > 40 and conf._secret_key() == first
    assert stat.S_IMODE(os.stat(tmp_path / "secret_key").st_mode) == 0o600
    monkeypatch.setenv("DJANGO_SECRET_KEY", "change-me")  # the example value is not a key
    assert conf._secret_key() == first
    monkeypatch.setenv("DJANGO_SECRET_KEY", "from-the-environment")
    assert conf._secret_key() == "from-the-environment"
