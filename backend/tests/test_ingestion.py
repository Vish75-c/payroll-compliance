"""Both ways in give the same data; field names differ between tools; values are read exactly."""
from datetime import date
from decimal import Decimal

import pytest

from payroll.features.hr_data.canonical import heuristic_mapping, month_column
from payroll.features.hr_data.normalize import build_snapshot, unpaid_days_for, validate_mapping
from payroll.features.hr_data.values import parse_amount, parse_date
from payroll_engine.periods import Period

STRUCTURE = {"components": [{"code": "basic", "share": "0.5"}, {"code": "hra", "share": "0.2"}, {"code": "special_allowance", "share": "remainder"}]}


@pytest.mark.parametrize("header", ["Basic", "Basic Salary", "Basic Pay", "BASIC (₹)", "basic_salary"])
def test_basic_pay_has_many_names(header):
    assert heuristic_mapping(["Emp Code", header])[header]["target"] == "comp.basic"


def test_horilla_like_and_keka_like_headers():
    m = heuristic_mapping(["Employee", "Leave Type", "Start Date", "End Date", "Requested Days", "Status"], "leave")
    assert m["Leave Type"]["target"] == "leave.type"
    assert m["Start Date"]["target"] == "leave.start_date"
    assert m["Requested Days"]["target"] == "leave.days"
    assert m["Status"]["target"] == "leave.status"


def test_employee_status_column_is_not_taken_for_leave():
    m = heuristic_mapping(["Emp Code", "Name", "Status"])
    assert m["Status"]["target"] == "ignore"


def test_month_columns():
    assert month_column("LOP days - July 2025", None) == "attendance.unpaid_days@2025-07"
    assert month_column("June unpaid leave", 2025) == "attendance.unpaid_days@2025-06"


@pytest.mark.parametrize("text,value", [("₹45,000.00", "45000"), ("Rs. 1,50,000", "150000"), ("10k", "10000"), ("1.5 lakh", "150000"), ("(500)", "-500")])
def test_amounts(text, value):
    assert parse_amount(text) == Decimal(value)


@pytest.mark.parametrize("text", ["2025-07-14", "14/07/2025", "14-Jul-2025", "Jul. 14, 2025", "July 14, 2025", "14 Jul 2025"])
def test_dates(text):
    assert parse_date(text) == date(2025, 7, 14)


def test_spreadsheet_with_one_row_per_employee_and_a_raise_column():
    rows = [{"Name": "Priya Sharma", "Salary": "80000", "New salary": "90000", "Raise from": "01/05/2025", "LOP Jul 2025": "0"},
            {"Name": "Arjun Mehta", "Salary": "18000", "New salary": "", "Raise from": "", "LOP Jul 2025": "3"}]
    mapping = {h: v["target"] for h, v in heuristic_mapping(list(rows[0])).items()}
    assert mapping == {"Name": "employee.name", "Salary": "comp.monthly_gross", "New salary": "revision.monthly_gross",
                       "Raise from": "revision.effective_from", "LOP Jul 2025": "attendance.unpaid_days@2025-07"}
    data, checks = build_snapshot([{"name": "Sheet1", "headers": list(rows[0]), "rows": rows, "mapping": mapping}], STRUCTURE, date(2025, 7, 10))
    priya = [c for c in data["compensation"] if c["employee_code"].endswith("priya-sharma")]
    assert [c["components"]["basic"] for c in priya] == ["40000", "45000"]
    assert priya[1]["recorded_on"] == "2025-07-10" and priya[1]["recorded_on_assumed"]
    arjun = next(e for e in data["employees"] if e["name"] == "Arjun Mehta")
    assert unpaid_days_for(data, arjun["code"], Period(2025, 7)) == 3


def test_leave_status_and_type_filters():
    rows = [{"ID": "E1", "Type": "Loss of Pay", "From": "2025-07-01", "To": "2025-07-02", "Days": "2", "Status": "Approved"},
            {"ID": "E1", "Type": "Loss of Pay", "From": "2025-07-10", "To": "2025-07-10", "Days": "1", "Status": "Rejected"},
            {"ID": "E1", "Type": "Casual Leave", "From": "2025-07-20", "To": "2025-07-20", "Days": "1", "Status": "Approved"},
            {"ID": "E1", "Type": "LWP", "From": "2025-07-30", "To": "2025-08-02", "Days": "4", "Status": "Approved"}]
    emp = [{"ID": "E1", "Name": "A", "Basic": "1000"}]
    tables = [{"name": "Emp", "headers": ["ID", "Name", "Basic"], "rows": emp, "mapping": {"ID": "employee.code", "Name": "employee.name", "Basic": "comp.basic"}},
              {"name": "Leave", "headers": list(rows[0]), "rows": rows, "dataset_hint": "leave",
               "mapping": {"ID": "employee.code", "Type": "leave.type", "From": "leave.start_date", "To": "leave.end_date", "Days": "leave.days", "Status": "leave.status"}}]
    data, _ = build_snapshot(tables, STRUCTURE, date(2025, 8, 1))
    assert unpaid_days_for(data, "E1", Period(2025, 7)) == 2 + 2  # 2 approved LOP + 2 of the 4 LWP days that fall in July
    assert unpaid_days_for(data, "E1", Period(2025, 8)) == 2


@pytest.mark.parametrize("recorded,conflict", [("2025-07-20", False), ("2025-07-05", True), ("", True)])
def test_salary_corrections_require_distinct_recording_dates(recorded, conflict):
    original = {"ID": "E1", "Name": "Test Employee", "Gender": "F", "State": "KA", "Joined": "2025-07-16",
                "Basic": "30000", "Effective": "2025-07-16", "Recorded": "2025-07-05"}
    correction = dict(original, Basic="60000", Recorded=recorded)
    copy_without_date = dict(original, Recorded="")
    mapping = {"ID": "employee.code", "Name": "employee.name", "Gender": "employee.gender",
               "State": "employee.work_state", "Joined": "employee.date_of_joining", "Basic": "comp.basic",
               "Effective": "comp.effective_from", "Recorded": "comp.recorded_on"}
    table = {"name": "Contracts", "headers": list(original), "rows": [original, correction, copy_without_date],
             "mapping": mapping}

    data, checks = build_snapshot([table], STRUCTURE, date(2025, 7, 31))

    assert any(c["code"] == "salary_conflict" and c["level"] == "error" for c in checks) is conflict
    if not conflict:
        rows = data["compensation"]
        assert [(r["recorded_on"], r["components"]["basic"]) for r in rows] == [
            ("2025-07-05", "30000"), ("2025-07-20", "60000"),
        ]


def test_completeness_checks_catch_missing_rows():
    tables = [{"name": "People", "headers": ["ID", "Name"], "rows": [{"ID": "1", "Name": "A"}], "read_info": {"total_shown": 2},
               "mapping": {"ID": "employee.code", "Name": "employee.name"}, "dataset_hint": "employees"}]
    _, checks = build_snapshot(tables, STRUCTURE, date(2025, 7, 1))
    codes = {c["code"] for c in checks if c["level"] == "error"}
    assert {"count_mismatch", "no_salary"} <= codes


def test_mapping_validation():
    assert validate_mapping({"A": "comp.basic", "B": "comp.basic"}, ["A", "B"])
    assert validate_mapping({"A": "comp.basic"}, ["A"])  # no employee identity


@pytest.mark.django_db
def test_spreadsheet_and_hr_tool_give_the_same_facts(demo):
    """The Demo HRMS shows the same people with different column names and formats."""
    import json

    from .conftest import REPO

    company, sheet_snapshot = demo
    seed = json.loads((REPO / "data/brickline/seed.json").read_text())
    names = {e["code"]: e["name"] for e in seed["employees"]}
    people = [{"Emp Code": e["code"], "Full Name": e["name"], "Gender": "Female" if e["gender"] == "F" else "Male", "Location": e["location"],
               "Joined On": date.fromisoformat(e["doj"]).strftime("%d %b %Y"),
               "Last Working Day": date.fromisoformat(e["doe"]).strftime("%d %b %Y") if e.get("doe") else ""} for e in seed["employees"]]
    pay = [{"Emp Code": s["code"], "Employee": names[s["code"]], "Basic Salary": f"₹{s['basic']:,}.00", "House Rent Allowance": f"₹{s['hra']:,}.00",
            "Special Allow.": f"₹{s['special']:,}.00", "Effective Date": date.fromisoformat(s["effective_from"]).strftime("%d %b %Y"),
            "Approved On": date.fromisoformat(s["recorded_on"]).strftime("%d %b %Y")} for s in seed["salaries"]]
    leave = [{"Emp Code": lv["code"], "Leave Type": lv["type"], "Start": lv["from"], "End": lv["to"], "No. of Days": str(lv["days"]), "Status": lv["status"]}
             for lv in seed["leave"]]
    tables = []
    for name, rows, hint in (("People", people, "employees"), ("Pay", pay, "compensation"), ("Time off", leave, "leave")):
        guess = heuristic_mapping(list(rows[0]), hint)
        tables.append({"name": name, "headers": list(rows[0]), "rows": rows, "dataset_hint": hint, "mapping": {h: g["target"] for h, g in guess.items()}})
    data, _ = build_snapshot(tables, STRUCTURE, date.today())
    from payroll.features.hr_data.snapshots import content_of
    assert content_of(data) == content_of(sheet_snapshot.data)


def test_horilla_style_contract_list_gives_components_and_first_seen_dates():
    """Horilla lists contracts with the employee's name and a single wage; no 'recorded on' column."""
    contracts = [{"Contract": "Priya 2025", "Employee": "Priya Sharma", "Start Date": "Apr. 1, 2025", "Contract wage": "80000.0", "Status": "Expired"},
                 {"Contract": "Priya raise", "Employee": "Priya Sharma", "Start Date": "May 1, 2025", "Contract wage": "90000.0", "Status": "Active"}]
    people = [{"Badge ID": "BL001", "Employee": "Priya Sharma", "Work location": "Bengaluru, Karnataka", "Joining Date": "Jun. 14, 2021",
               "Gender": "Female"}]
    mapping_c = {"Contract": "ignore", "Employee": "employee.name", "Start Date": "comp.effective_from", "Contract wage": "comp.monthly_gross", "Status": "ignore"}
    mapping_p = {"Badge ID": "employee.code", "Employee": "employee.name", "Work location": "employee.work_state",
                 "Joining Date": "employee.date_of_joining", "Gender": "employee.gender"}
    tables = [{"name": "Employees", "headers": list(people[0]), "rows": people, "mapping": mapping_p, "dataset_hint": "employees"},
              {"name": "Contracts", "headers": list(contracts[0]), "rows": contracts, "mapping": mapping_c, "dataset_hint": "compensation"}]
    data, checks = build_snapshot(tables, STRUCTURE, date(2025, 7, 3))
    comp = data["compensation"]
    assert [c["employee_code"] for c in comp] == ["BL001", "BL001"]
    assert comp[1]["components"] == {"basic": "45000", "hra": "18000", "special_allowance": "27000"}
    assert comp[1]["recorded_on"] == "2025-07-03" and comp[1]["recorded_on_assumed"]
    assert not [c for c in checks if c["level"] == "error"]


def test_remainder_of_gross_is_split_by_structure():
    rows = [{"ID": "E1", "Name": "A", "Basic": "9000", "Gross": "18000"}]
    mapping = {"ID": "employee.code", "Name": "employee.name", "Basic": "comp.basic", "Gross": "comp.monthly_gross"}
    data, _ = build_snapshot([{"name": "S", "headers": list(rows[0]), "rows": rows, "mapping": mapping}], STRUCTURE, date(2025, 7, 1))
    assert data["compensation"][0]["components"] == {"basic": "9000", "hra": "3600", "special_allowance": "5400"}


def test_horilla_default_columns_map_without_help():
    contracts = heuristic_mapping(["Contract", "Employee", "Start Date", "End Date", "Wage Type", "Contract wage", "Filing Status", "Salary Structure", "Status"], "compensation")
    assert contracts["Employee"]["target"] == "employee.name"
    assert contracts["Start Date"]["target"] == "comp.effective_from"
    assert contracts["Contract wage"]["target"] == "comp.monthly_gross"
    leave = heuristic_mapping(["Employee", "Leave Type", "Start Date", "End Date", "Requested Days", "Status"], "leave")
    assert leave["Start Date"]["target"] == "leave.start_date" and leave["End Date"]["target"] == "leave.end_date"


def test_name_with_badge_in_one_column():
    rows = [{"Employee": "Priya Sharma (BL001)", "Basic": "1"}]
    data, _ = build_snapshot([{"name": "E", "headers": ["Employee", "Basic"], "rows": rows,
                               "mapping": {"Employee": "employee.name", "Basic": "comp.basic"}}], STRUCTURE, date(2025, 7, 1))
    assert data["employees"][0] == {"code": "BL001", "name": "Priya Sharma"}


def test_rejected_leave_cannot_be_confirmed_as_counting(demo):
    """Counting a rejected or cancelled leave would cut pay for a day that was worked (Sneha's July in the demo)."""
    import pytest as _pytest

    from payroll.demo import setup
    from payroll.features.hr_data import jobs
    from payroll.shared.permissions import RuleViolation

    maker = setup.system_user("maker")
    csv = (b"Employee ID,Leave Type,From,To,Days,Status\n"
           b"BL002,Loss of Pay,2025-07-14,2025-07-16,3,Approved\n"
           b"BL006,Loss of Pay,2025-07-28,2025-07-28,1,Rejected\n"
           b"BL005,Loss of Pay,2025-07-10,2025-07-10,1,Cancelled\n")
    company, _ = demo
    job = jobs.run_spreadsheet_job(company, maker, [("leave.csv", csv, "leave")])
    table = job.tables.get()
    mapping = {"Employee ID": "employee.code", "Leave Type": "leave.type", "From": "leave.start_date",
               "To": "leave.end_date", "Days": "leave.days", "Status": "leave.status"}
    unpaid = {"leave.type": {"Loss of Pay": "unpaid"}}

    with _pytest.raises(RuleViolation, match="'Cancelled', 'Rejected' was not taken"):
        jobs.confirm(table, maker, mapping, {**unpaid, "leave.status": {"Approved": "count", "Rejected": "count", "Cancelled": "count"}})

    fm = jobs.confirm(table, maker, mapping, {**unpaid, "leave.status": {"Approved": "count", "Rejected": "skip", "Cancelled": "skip"}})
    assert fm.confirmed_at
    # Left to the suggested defaults, rejected and cancelled leave are ignored.
    assert jobs.default_value_maps(table.rows, mapping)["leave.status"] == {"Approved": "count", "Cancelled": "skip", "Rejected": "skip"}


def test_name_with_id_in_brackets_matches_people_whichever_way_it_is_mapped():
    """Horilla shows "Priya Sharma (BL001)". An AI guess may map that column to the name on one screen and to the
    employee ID on another; both must find the same person, or the leave would belong to nobody."""
    employees = [{"Employee": "Priya Sharma (BL001)", "Department": "Design"}, {"Employee": "Arjun Mehta (BL002)", "Department": "Ops"}]
    leave = [{"Employee": "Arjun Mehta (BL002)", "Leave Type": "Loss of Pay", "Start Date": "14/07/2025", "End Date": "16/07/2025",
              "Requested Days": "3", "Status": "Approved"}]
    salary = [{"Employee": "Arjun Mehta (BL002)", "Start Date": "01/04/2025", "Contract wage": "18000"}]
    tables = [
        {"name": "Employees", "headers": list(employees[0]), "rows": employees, "dataset_hint": "employees",
         "mapping": {"Employee": "employee.name", "Department": "employee.department"}},
        {"name": "Salary", "headers": list(salary[0]), "rows": salary,
         "mapping": {"Employee": "employee.name", "Start Date": "comp.effective_from", "Contract wage": "comp.monthly_gross"}},
        {"name": "Leave", "headers": list(leave[0]), "rows": leave,
         "mapping": {"Employee": "employee.code", "Leave Type": "leave.type", "Start Date": "leave.start_date",
                     "End Date": "leave.end_date", "Requested Days": "leave.days", "Status": "leave.status"},
         "value_maps": {"leave.type": {"Loss of Pay": "unpaid"}, "leave.status": {"Approved": "count"}}},
    ]
    data, checks = build_snapshot(tables, STRUCTURE, date(2025, 7, 20))
    assert not [c for c in checks if c["code"] == "unknown_employee"]
    assert [e["code"] for e in data["employees"]] == ["BL001", "BL002"]
    assert [(r["employee_code"], r["days"]) for r in data["leave"]] == [("BL002", "3")]


@pytest.mark.django_db
def test_new_columns_named_like_payroll_fields_are_not_dropped_without_a_profile(settings):
    """What happened with Horilla while profiles were switched off: the employee list was mapped with its few
    columns, then "detail pages" added Gender and Joining Date from each employee's own page. Those columns must be
    pre-filled and the mapping confirmed again, not silently reused with the new columns ignored."""
    settings.HR_TOOL_PROFILES = False
    from payroll.demo import setup
    from payroll.features.hr_data import jobs
    from payroll.models import Connector, IngestionJob

    setup.ensure_users()
    company = setup.ensure_company("t", "Test Co", "KA")
    maker = setup.system_user("maker")
    conn = Connector.objects.create(company=company, name="Horilla", kind="browser", system_name="Horilla", base_url="http://x/")

    def read(headers, rows):
        job = IngestionJob.objects.create(company=company, connector=conn, kind="browser", source_system="Horilla", started_by=maker)
        jobs.store_tables(job, [{"name": "Employees screen", "dataset_hint": "employees", "headers": headers, "rows": rows}])
        return job.tables.get()

    first = read(["Employee", "Department", "Email"], [{"Employee": "Priya Sharma (BL001)", "Department": "Design", "Email": "p@x"}])
    jobs.confirm(first, maker, {"Employee": "employee.name", "Department": "employee.department", "Email": "ignore"})

    second = read(["Employee", "Department", "Email", "Gender", "Joining Date", "End Date"],
                  [{"Employee": "Priya Sharma (BL001)", "Department": "Design", "Email": "p@x", "Gender": "Female",
                    "Joining Date": "01/04/2024", "End Date": ""}])
    m = second.mapping
    assert m.confirmed_at is None  # a person must look at the new columns
    assert set(m.options["ask_model_for"]) == {"Gender", "Joining Date", "End Date"}  # nobody has said what they mean
    assert m.mapping["Gender"] == "employee.gender"  # pre-filled by its exact name until the model answers
    assert any(i["code"] == "new_columns" for i in second.read_info.get("issues", []))

    # Suggest mapping: the model (here the rule-based matcher, AI is off in tests) is asked about the new columns only.
    jobs.suggest(second.job, maker)
    m.refresh_from_db()
    assert m.options["ask_model_for"] == [] and m.suggested_by is not None and m.confirmed_at is None
    assert m.mapping["Gender"] == "employee.gender" and m.mapping["Joining Date"] == "employee.date_of_joining"
    assert m.mapping["End Date"] == "ignore"  # a leave field does not belong in an employee table
    assert m.mapping["Department"] == "employee.department" and m.mapping["Email"] == "ignore"  # confirmed: kept


@pytest.mark.django_db
def test_confirming_a_mapping_again_keeps_one_review_note_on_the_ai_answer():
    """One AI answer serves the same screen read after read. Each confirmation used to add another identical
    "Confirmed by maker without changes" check to it, so the AI log repeated it again and again."""
    from payroll.demo import setup
    from payroll.features.ai.present import ai as present_ai
    from payroll.features.hr_data import jobs
    from payroll.models import IngestionJob

    setup.ensure_users()
    company = setup.ensure_company("t", "Test Co", "KA")
    maker = setup.system_user("maker")
    job = IngestionJob.objects.create(company=company, kind="spreadsheet", source_system="Sheet", started_by=maker)
    jobs.store_tables(job, [{"name": "People", "dataset_hint": "employees", "headers": ["Emp Code", "Name"],
                             "rows": [{"Emp Code": "E1", "Name": "Asha"}]}])
    jobs.suggest(job, maker)
    table = job.tables.get()
    for _ in range(3):
        table.refresh_from_db()
        jobs.confirm(table, maker, table.mapping.mapping)
    answer = table.mapping.suggested_by
    answer.refresh_from_db()
    notes = [v for v in answer.validation if v.get("code") == "confirmed"]
    assert len(notes) == 1 and notes[0]["times"] == 3 and "confirmed 3 times" in notes[0]["message"]

    # Answers saved before the fix still hold the copies: the AI log shows them once, with a count.
    answer.validation = [{"level": "info", "message": "Confirmed by maker without changes"}] * 7
    shown = present_ai(answer)["validation"]
    assert shown == [{"level": "info", "message": "Confirmed by maker without changes (7 times)", "repeated": 7}]
