"""Known HR tool profiles: exports and screens of a known tool are mapped without AI; AI is per company and
never the Claude Code CLI in production; layouts companies map themselves become drafts for review."""
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from .conftest import REPO

pytestmark = pytest.mark.django_db
FIXTURES = REPO / "integrations/horilla/fixtures"


def login(username):
    c = Client()
    assert c.post("/api/login", {"username": username, "password": f"{username}123"}, content_type="application/json").status_code == 200
    return c


def fixture(name, drop_column=None):
    content = (FIXTURES / name).read_bytes()
    if drop_column:
        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        col = next(c.column for c in ws[1] if c.value == drop_column)
        ws.delete_cols(col)
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()
    return SimpleUploadedFile(name, content)


@pytest.fixture
def no_model_calls(settings, monkeypatch):
    """A model is configured, but any call to it fails the test."""
    from payroll.features.ai import client

    settings.AI_PROVIDER = "anthropic"

    def refuse(*a, **k):
        raise AssertionError("an AI model was called")

    monkeypatch.setattr(client, "_call_claude", refuse)
    monkeypatch.setattr(client, "_call_claude_code", refuse)


def test_horilla_exports_are_mapped_from_the_profile_and_july_matches_answer_sheet_a(demo, no_model_calls, settings):
    from payroll.demo.answer_sheet import _find_employee, read_expected
    from payroll.models import AIInteraction, DataSnapshot
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots

    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {
        "employees": fixture("employee_export.xlsx"), "compensation": fixture("Contract_export.xlsx"), "leave": fixture("Leave_requests.xlsx"),
    }).json()
    tables = {t["name"]: t for t in job["tables"]}
    assert {n: t["mapping"]["options"]["profile"].split("@")[0] for n, t in tables.items()} == {
        "Employees": "horilla/employees-export", "Salary": "horilla/contracts-export", "Leave": "horilla/leave-export"}
    salary = tables["Salary"]["mapping"]["mapping"]
    assert salary["Basic Salary"] == "comp.monthly_gross" and salary["Contract"] == "ignore"
    assert tables["Employees"]["mapping"]["mapping"]["Basic Salary"] == "ignore"
    assert not any(t["mapping"]["confirmed"] for t in tables.values())  # pre-filled; a person confirms
    assert job["status"] == "needs_mapping"
    assert AIInteraction.objects.filter(purpose="field_mapping").count() == 0

    for t in tables.values():
        r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"], "value_maps": t["mapping"]["value_maps"]},
                       content_type="application/json")
        assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [c for c in snap["checks"] if c["level"] == "error"], snap["checks"]
    data = DataSnapshot.objects.get(pk=snap["id"]).data
    priya = sorted((r["effective_from"], r["components"]["basic"]) for r in data["compensation"] if r["employee_code"] == "BL001")
    assert priya == [("2025-04-01", "40000"), ("2025-05-01", "45000")]
    snapshots.approve(DataSnapshot.objects.get(pk=snap["id"]), setup.system_user("maker"))

    settings.AI_PROVIDER = "off"  # July's bonus instruction goes to the instruction parser, which is not about mapping
    choices = json.loads((REPO / "answer_sheets/case_A/choices.json").read_text())
    july = setup.run_month(company, "2025-07", choices.get("instructions", {}).get("2025-07", []), finalize=False,
                           knowledge_date=timezone.localdate(), log=lambda *a: None)
    expected = [e for e in read_expected(REPO / "answer_sheets/case_A/expected.csv", 2025) if e.month == "2025-07"]
    wrong = [(e.employee_id, e.item, str(e.expected), _find_employee(july.result, e)["amounts"].get(e.item_key))
             for e in expected if Decimal(_find_employee(july.result, e)["amounts"].get(e.item_key, "0")) != e.expected]
    assert expected and not wrong, wrong


def test_a_leave_export_without_leave_type_says_how_to_fix_it(demo, no_model_calls):
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"leave": fixture("Leave_requests.xlsx", drop_column="Leave Type")}).json()
    issues = job["tables"][0]["read_info"]["issues"]
    assert [i["code"] for i in issues] == ["profile_missing_column"]
    assert "Leave Type ticked" in issues[0]["message"]


def test_unknown_layouts_still_use_rules_or_ai_and_masked_samples(demo, settings, monkeypatch):
    from payroll.features.ai import client
    from payroll.features.hr_data import jobs
    from payroll.models import IngestionJob
    from payroll.demo import setup

    company, _ = demo
    settings.AI_PROVIDER = "anthropic"
    sent = []

    def fake_claude(system, prompt, schema):
        sent.append(prompt)
        cols = json.loads(prompt)["columns"]
        return {"columns": [{"column": c, "target": "ignore", "confidence": 0.5, "reason": "x"} for c in cols]}, {"model_served": "test"}

    monkeypatch.setattr(client, "_call_claude", fake_claude)
    maker = setup.system_user("maker")
    job = jobs.run_spreadsheet_job(company, maker, [("staff.csv", b"Staff No,Full Name,Monthly Pay\nX-17,Priya Sharma,80000\n", "")])
    assert job.tables.get().mapping is None  # no profile knows this layout
    jobs.suggest(job, maker)
    assert sent and "Priya" not in sent[0] and "80000" not in sent[0] and "Aaaaa Aaaaaa" in sent[0]

    # The company turns AI off: nothing is sent, the rule-based matcher answers, and it says why.
    company.ai_mode = "off"
    company.save()
    sent.clear()
    job = IngestionJob.objects.get(pk=jobs.run_spreadsheet_job(company, maker, [("staff2.csv", b"Staff No,Full Name,Monthly Pay\nX-18,Ravi Kumar,21000\n", "")]).pk)
    jobs.suggest(job, maker, force=True)
    interaction = job.tables.get().mapping.suggested_by
    assert not sent and interaction.provider == "rule-based"
    assert any("turned off for this company" in v["message"] for v in interaction.validation)


def test_claude_code_cli_is_for_development_only(settings, monkeypatch):
    from payroll.features.ai import client

    monkeypatch.setattr(client, "_claude_code_bin", lambda: "/usr/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(client.Path, "home", lambda: REPO / "no-such-home")
    settings.AI_PROVIDER, settings.DEBUG, settings.AI_ALLOW_CLI = "claude-code", True, False
    assert client.provider() == client.CLAUDE_CODE
    settings.DEBUG = False
    assert client.provider() == client.RULE_BASED
    settings.AI_PROVIDER = "auto"
    assert client.provider() == client.RULE_BASED
    settings.AI_ALLOW_CLI = True
    assert client.provider() == client.CLAUDE_CODE


def test_profile_files_are_checked_when_loaded(tmp_path):
    from payroll.features.hr_data.profiles import ProfileError, load_profiles

    bad = {"system": "acme", "name": "Acme HR", "verified_on": "2026-11-22", "layouts": [
        {"id": "pay", "kind": "export", "dataset": "compensation", "match_on": ["Pay"], "columns": {"Pay": "comp.salary_in_gold"}}]}
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme/profile.json").write_text(json.dumps(bad))
    with pytest.raises(ProfileError, match="unknown payroll field"):
        load_profiles(str(tmp_path))
    bad["layouts"][0]["columns"] = {"Pay": "comp.monthly_gross"}
    bad["layouts"][0]["match_on"] = ["Pay", "Employee"]
    (tmp_path / "acme/profile.json").write_text(json.dumps(bad))
    load_profiles.cache_clear()
    with pytest.raises(ProfileError, match="match_on"):
        load_profiles(str(tmp_path))
    load_profiles.cache_clear()


def test_known_tools_api_connector_and_company_ai_switch(demo):
    from payroll.features.hr_data.profiles import detect_system

    company, _ = demo
    maker, checker = login("maker"), login("checker")
    known = maker.get("/api/hr-tool-profiles").json()
    assert [p["system"] for p in known] == ["horilla"] and known[0]["has_screens"]
    conn = maker.post(f"/api/companies/{company.pk}/connectors", {"name": "Our Horilla", "base_url": "http://hr.example.test/",
                                                                  "profile": "horilla"}, content_type="application/json").json()
    assert conn["profile"] == "horilla" and conn["system_name"] == "Horilla"
    assert "contract_status=expired" in conn["recipe"]["datasets"]["compensation"]["url"] and not conn["recipe_confirmed_at"]
    assert detect_system("Horilla | 00:00:00").system == "horilla" and detect_system("Demo HRMS") is None

    assert checker.patch(f"/api/companies/{company.pk}", {"ai_mode": "off"}, content_type="application/json").status_code == 403
    out = maker.patch(f"/api/companies/{company.pk}", {"ai_mode": "off"}, content_type="application/json").json()
    assert out["ai_mode"] == "off" and out["ai_mode_changed_by"]["username"] == "maker"


def test_layouts_companies_mapped_become_drafts_without_row_data(demo):
    from payroll.features.hr_data import jobs
    from payroll.management.commands.profile_candidates import collect
    from payroll.demo import setup

    company, _ = demo
    other = setup.ensure_company("acme", "Acme Studio", "KA")
    maker = setup.system_user("maker")
    for co, row in ((company, b"P-1,Priya Sharma,80000,2025-04-01"), (other, b"Q-9,Zoya Khan,55000,2025-04-01")):
        job = jobs.run_spreadsheet_job(co, maker, [("pay.csv", b"Staff No,Full Name,Monthly Pay,Pay From\n" + row + b"\n", "compensation")])
        t = job.tables.get()
        jobs.confirm(t, maker, {"Staff No": "employee.code", "Full Name": "employee.name", "Monthly Pay": "comp.monthly_gross",
                                "Pay From": "comp.effective_from"})
    drafts = collect()
    layout = next(lay for lay in drafts["spreadsheet"]["layouts"] if "Monthly Pay" in lay["columns"])
    assert layout["seen_at_companies"] == 2 and layout["agreeing_companies"] == 2 and layout["dataset"] == "compensation"
    text = json.dumps(drafts)
    assert "Priya" not in text and "Zoya" not in text and "80000" not in text
    # The Brickline workbook was confirmed too, but Horilla's own layouts are never drafted again.
    assert "horilla" not in drafts


def test_a_company_mapping_that_differs_from_the_profile_is_flagged_until_fixed(demo, no_model_calls):
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"compensation": fixture("Contract_export.xlsx")}).json()
    t = job["tables"][0]
    wrong = {**t["mapping"]["mapping"], "Basic Salary": "comp.basic"}  # what was confirmed here before profiles existed
    job = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": wrong}, content_type="application/json").json()
    assert "'Basic Salary' is Basic pay (monthly) here, the profile says Monthly gross salary" in job["tables"][0]["read_info"]["issues"][0]["message"]

    # Next month's export: the company's own mapping is reused (it wins), and the difference is pointed out again.
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"compensation": fixture("Contract_export.xlsx")}).json()
    t = job["tables"][0]
    assert t["mapping"]["confirmed"] and t["mapping"]["mapping"]["Basic Salary"] == "comp.basic"
    assert [i["code"] for i in t["read_info"]["issues"]] == ["differs_from_profile"]
    fixed = {**t["mapping"]["mapping"], "Basic Salary": "comp.monthly_gross"}
    job = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": fixed}, content_type="application/json").json()
    assert not job["tables"][0]["read_info"].get("issues")


def test_fields_from_an_employee_page_never_become_pay():
    """An employee's page in Horilla also shows a "Basic Salary" (0): only employee fields are taken from it."""
    from payroll.features.hr_data.profiles import match_layout

    m = match_layout(["Employee", "Email", "Department", "Reporting Manager", "Joining Date", "Gender", "Work Location",
                      "Basic Salary", "Monthly Gross", "Date of birth"], "employees", "horilla")
    assert m.layout.id == "employees-list"
    assert m.mapping["Joining Date"] == "employee.date_of_joining" and m.mapping["Work Location"] == "employee.work_state"
    assert m.mapping["Basic Salary"] == "ignore" and m.mapping["Monthly Gross"] == "ignore"


def test_missing_joining_dates_locations_and_genders_block_the_data():
    from datetime import date

    from payroll.features.hr_data.normalize import build_snapshot

    structure = {"components": [{"code": "basic", "share": "0.50"}, {"code": "hra", "share": "0.20"},
                                {"code": "special_allowance", "share": "remainder"}]}
    tables = [{"name": "People", "headers": ["ID", "Name", "Salary"], "dataset_hint": "employees",
               "rows": [{"ID": "E1", "Name": "Asha Nair", "Salary": "30000"}, {"ID": "E2", "Name": "Dev Shah", "Salary": "40000"}],
               "mapping": {"ID": "employee.code", "Name": "employee.name", "Salary": "comp.monthly_gross"}}]
    _, checks = build_snapshot(tables, structure, date(2025, 7, 1))
    codes = {c["code"]: c for c in checks}
    # Nothing corrects a month after it is paid, so incomplete data is stopped before any month uses it.
    assert codes["no_joining_date"]["level"] == "error" and "2 of 2" in codes["no_joining_date"]["message"]
    assert codes["no_work_state"]["level"] == "error"
    assert codes["no_gender"]["level"] == "error" and "2 of 2" in codes["no_gender"]["message"]


def test_a_leave_file_in_the_employees_box_is_explained_in_one_message(demo, no_model_calls):
    """What happened on 22 Nov 2026: a leave export uploaded as Employees. One clear error, not one per salary row."""
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {
        "employees": fixture("Leave_requests.xlsx"), "compensation": fixture("Contract_export.xlsx"),
        "leave": fixture("Leave_requests.xlsx", drop_column="Leave Type"),
    }).json()
    for t in job["tables"]:
        maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"], "value_maps": t["mapping"]["value_maps"]},
                   content_type="application/json")
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    unknown = [c for c in snap["checks"] if c["code"] == "unknown_employee"]
    assert len(unknown) == 1 and "the employee list is the wrong file" in unknown[0]["message"]
    codes = {c["code"] for c in snap["checks"]}
    assert {"wrong_kind_of_data", "profile_missing_column"} <= codes


def test_a_new_meaningful_column_is_offered_not_dropped(demo, no_model_calls):
    """First a leave export without Leave Type is confirmed; next time the column is there. It must be used."""
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"leave": fixture("Leave_requests.xlsx", drop_column="Leave Type")}).json()
    t = job["tables"][0]
    maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"], "value_maps": t["mapping"]["value_maps"]},
               content_type="application/json")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"leave": fixture("Leave_requests.xlsx")}).json()
    t = job["tables"][0]
    assert t["mapping"]["mapping"]["Leave Type"] == "leave.type" and not t["mapping"]["confirmed"]
    assert [i["code"] for i in t["read_info"]["issues"]] == ["new_columns"]


def test_salary_seen_on_an_employee_list_is_shown_with_the_salary_data(demo):
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {
        "employees": fixture("employee_export.xlsx"), "compensation": fixture("Contract_export.xlsx")}).json()
    tables = {t["dataset_hint"]: t for t in maker.get(f"/api/jobs/{job['id']}").json()["tables"]}
    assert tables["employees"]["salary_columns"] == ["Basic Salary", "Salary Hour"]
    moved = tables["compensation"]["salary_from_employees"]
    assert [m["columns"] for m in moved] == [["Basic Salary", "Salary Hour"]] and len(moved[0]["rows"]) == 80
    assert moved[0]["rows"][0]["employee"] == "BL001" and set(moved[0]["rows"][0]["values"]) == {"Basic Salary", "Salary Hour"}

    # Only an employee list: a salary column there may be the only salary there is, so it stays with the employees.
    alone = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": fixture("employee_export.xlsx")}).json()
    only = maker.get(f"/api/jobs/{alone['id']}").json()["tables"][0]
    assert "salary_columns" not in only and "Basic Salary" in only["headers"]


def test_a_fact_missing_for_some_people_makes_only_them_wait():
    from datetime import date

    from payroll.features.hr_data.normalize import build_snapshot

    structure = {"components": [{"code": "basic", "share": "0.50"}, {"code": "hra", "share": "0.20"},
                                {"code": "special_allowance", "share": "remainder"}]}
    headers = ["ID", "Name", "Salary", "Joined", "State", "Gender"]
    rows = [{"ID": "E1", "Name": "Asha Nair", "Salary": "30000", "Joined": "2024-01-01", "State": "Karnataka", "Gender": "Female"},
            {"ID": "E2", "Name": "Dev Shah", "Salary": "40000", "Joined": "", "State": "", "Gender": "Male"},
            {"ID": "E3", "Name": "Ira Rao", "Salary": "", "Joined": "2024-03-01", "State": "Karnataka", "Gender": ""}]
    mapping = {"ID": "employee.code", "Name": "employee.name", "Salary": "comp.monthly_gross", "Joined": "employee.date_of_joining",
               "State": "employee.work_state", "Gender": "employee.gender"}
    data, checks = build_snapshot([{"name": "People", "headers": headers, "rows": rows, "mapping": mapping, "dataset_hint": "employees"}],
                                  structure, date(2025, 7, 1))
    codes = {c["code"]: c for c in checks}
    assert data["incomplete"] == {"E2": ["date_of_joining", "work_state"], "E3": ["salary", "gender"]}
    for code in ("no_salary", "no_joining_date", "no_work_state", "no_gender"):
        assert codes[code]["level"] == "warning", code
    assert codes["no_joining_date"]["detail"]["employees"] == ["E2"] and "everyone else is paid" in codes["no_joining_date"]["message"]
    assert not [c for c in checks if c["level"] == "error"]  # the data can be reviewed; E1 is paid

    full = [dict(r, Joined=r["Joined"] or "2024-01-01", State=r["State"] or "Karnataka", Gender=r["Gender"] or "Female",
                 Salary=r["Salary"] or "35000") for r in rows]
    data, _ = build_snapshot([{"name": "People", "headers": headers, "rows": full, "mapping": mapping, "dataset_hint": "employees"}],
                             structure, date(2025, 7, 1))
    assert "incomplete" not in data  # complete data looks exactly as before
