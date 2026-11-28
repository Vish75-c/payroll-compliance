"""The employee record: PAN, bank details, UAN, ESI number, date of birth and each person's PF choices, mapped from any
HR tool's columns and checked only when the HR data includes them. A missing bank account makes a person wait; a
missing PAN means TDS of at least 20%; the rest are warnings. The PF engine uses each person's choices."""
from datetime import date
from decimal import Decimal

import pytest

from payroll.features.hr_data.canonical import heuristic_mapping
from payroll.features.hr_data.normalize import build_snapshot, payment_mode

from .conftest import REPO
from .test_engine import amounts, emp, run

STRUCTURE = {"components": [{"code": "basic", "share": "0.50"}, {"code": "hra", "share": "0.20"},
                            {"code": "special_allowance", "share": "remainder"}]}
TEMPLATES = REPO / "fwdpayrollcomplianceauthorisationforaccesstocompet"


# --------------------------------------------------------------------------- mapping any HR tool's names

def test_the_importer_columns_map_to_the_new_fields():
    headers = ["Employee Number", "Name", "Date Of Birth", "Birthday", "PAN Number", "Bank Account Number", "IFSC Code",
               "Name As Per Bank Records", "Salary Payment Mode", "Universal Account Number", "PF Number", "PF Joining Date",
               "ESI Number", "Is employee eligible for PF?", "Is employee eligible for ESI?",
               "Is employee eligible for excess EPF contribution?", "Is existing member of EPS?", "Is International Employee"]
    got = {h: g["target"] for h, g in heuristic_mapping(headers, "employees").items()}
    assert got == {
        "Employee Number": "employee.code", "Name": "employee.name", "Date Of Birth": "employee.date_of_birth", "Birthday": "ignore",
        "PAN Number": "employee.pan", "Bank Account Number": "employee.bank_account", "IFSC Code": "employee.ifsc",
        "Name As Per Bank Records": "employee.name_at_bank", "Salary Payment Mode": "employee.payment_mode",
        "Universal Account Number": "employee.uan", "PF Number": "employee.pf_number", "PF Joining Date": "employee.pf_joining_date",
        "ESI Number": "employee.esi_number", "Is employee eligible for PF?": "employee.pf_applicable",
        "Is employee eligible for ESI?": "employee.esi_applicable", "Is employee eligible for excess EPF contribution?": "employee.pf_full_wage",
        "Is existing member of EPS?": "employee.eps_member", "Is International Employee": "employee.international_worker"}


def test_other_hr_tools_names_land_on_the_same_fields():
    got = {h: g["target"] for h, g in heuristic_mapping(["Emp Code", "Full Name", "Pan No", "A/C No", "Bank IFSC", "UAN No", "ESIC No",
                                                         "DOB", "Mode of Payment"], "employees").items()}
    assert (got["Pan No"], got["A/C No"], got["Bank IFSC"], got["UAN No"], got["ESIC No"], got["DOB"], got["Mode of Payment"]) == (
        "employee.pan", "employee.bank_account", "employee.ifsc", "employee.uan", "employee.esi_number", "employee.date_of_birth",
        "employee.payment_mode")


def test_payment_modes():
    assert [payment_mode(x) for x in ("Bank Transfer", "NEFT", "Cheque", "cash", "Demand Draft", "Cheque/Cash/DD/Bank Transfer", "")] == [
        "bank", "bank", "cheque", "cash", "dd", None, None]


# --------------------------------------------------------------------------- the checks

HEAD = ["ID", "Name", "Salary", "Joined", "State", "Gender", "PAN", "Account", "IFSC", "Mode", "UAN", "ESI", "DOB"]
MAP = {"ID": "employee.code", "Name": "employee.name", "Salary": "comp.monthly_gross", "Joined": "employee.date_of_joining",
       "State": "employee.work_state", "Gender": "employee.gender", "PAN": "employee.pan", "Account": "employee.bank_account",
       "IFSC": "employee.ifsc", "Mode": "employee.payment_mode", "UAN": "employee.uan", "ESI": "employee.esi_number",
       "DOB": "employee.date_of_birth"}


def _person(code, **kw):
    row = {"ID": code, "Name": code, "Salary": "40000", "Joined": "2020-01-01", "State": "Karnataka", "Gender": "Female",
           "PAN": "ABCDE1234F", "Account": "001234567890", "IFSC": "HDFC0001234", "Mode": "Bank Transfer", "UAN": "100200300400",
           "ESI": "1234567890", "DOB": "1990-05-01"}
    row.update(kw)
    return row


def _snapshot(rows, headers=HEAD, mapping=MAP):
    return build_snapshot([{"name": "People", "headers": headers, "rows": rows, "mapping": {h: mapping[h] for h in headers},
                            "dataset_hint": "employees"}], STRUCTURE, date(2025, 7, 1))


def test_a_missing_bank_account_makes_only_that_person_wait_and_the_rest_are_warnings():
    data, checks = _snapshot([
        _person("E1"),
        _person("E2", PAN="abcde 1234f", IFSC="A2003114567"),     # PAN fine once cleaned; the IFSC is not a valid code
        _person("E3", Mode="Cash", Account="", IFSC=""),           # paid in cash: no bank account needed
        _person("E4", PAN="ajkm09897l", UAN="", DOB="1960-03-01"),  # PAN not valid, no UAN, over 58
    ])
    codes = {c["code"]: c for c in checks}
    people = {e["code"]: e for e in data["employees"]}
    assert data["incomplete"] == {"E2": ["ifsc"]}  # only a missing bank detail stops pay
    assert people["E2"]["pan"] == "ABCDE1234F" and "ifsc" not in people["E2"]
    assert people["E3"]["payment_mode"] == "cash"
    assert codes["no_ifsc"]["level"] == "warning" and "1 given but not in the right format: E2" in codes["no_ifsc"]["message"]
    assert codes["no_pan"]["level"] == "warning" and "E4" in codes["no_pan"]["message"] and "206AA" in codes["no_pan"]["message"]
    assert "pan" not in people["E4"]  # an invalid PAN is not kept: it counts as missing
    assert codes["no_uan"]["level"] == "warning" and codes["no_uan"]["detail"]["employees"] == ["E4"]
    assert codes["eps_age"]["detail"]["employees"] == ["E4"]
    assert "ABCDE1234F" not in " ".join(c["message"] for c in checks)  # messages never show a PAN or an account number
    assert data["fields_present"] == ["pan", "bank_account", "ifsc", "payment_mode", "uan", "esi_number", "date_of_birth"]
    assert not [c for c in checks if c["level"] == "error"]


def test_bank_details_missing_for_everyone_is_a_mapping_problem():
    _, checks = _snapshot([_person("E1", IFSC=""), _person("E2", IFSC="")])
    assert next(c for c in checks if c["code"] == "no_ifsc")["level"] == "error"


def test_data_without_these_columns_is_noted_once_and_nobody_waits():
    head = ["ID", "Name", "Salary", "Joined", "State", "Gender"]
    data, checks = _snapshot([_person("E1"), _person("E2")], headers=head)
    codes = {c["code"] for c in checks}
    assert "incomplete" not in data and "fields_present" not in data  # the data looks exactly as before
    assert {"pan_not_in_data", "bank_not_in_data"} <= codes and not codes & {"no_pan", "no_ifsc", "no_uan"}


# --------------------------------------------------------------------------- the engine

def test_no_pan_means_tds_of_at_least_20_percent(statutory_versions):
    normal = amounts(run(emp("K", 150000), "2025-04", statutory_versions))
    no_pan = run(emp("K", 150000, no_pan=True), "2025-04", statutory_versions)
    a = amounts(no_pan)
    assert normal["tds"] == 12567 and a["tds"] == Decimal(150000 * 20 // 100)  # 20% of ₹1,50,000 is more than ₹12,567
    line = next(l for l in no_pan["lines"] if l["code"] == "tds")
    assert "206AA" in line["reason"] and "No valid PAN" in line["formula"]
    # Below the taxable limit there is no tax to deduct, PAN or not.
    assert amounts(run(emp("M", 30000, no_pan=True), "2025-06", statutory_versions))["tds"] == 0


def test_each_persons_pf_choices(statutory_versions):
    base = amounts(run(emp("A", 60000), "2025-06", statutory_versions))          # basic ₹30,000: PF on the ₹15,000 ceiling
    assert (base["pf_employee"], base["eps_employer"]) == (1800, 1250)
    full = amounts(run(emp("A", 60000, pf_full_wage=True), "2025-06", statutory_versions))
    assert full["pf_employee"] == 3600                                          # 12% of the whole ₹30,000
    iw = amounts(run(emp("A", 60000, international_worker=True), "2025-06", statutory_versions))
    assert iw["pf_employee"] == 3600
    no_eps = run(emp("A", 60000, eps_member=False), "2025-06", statutory_versions)
    assert amounts(no_eps)["eps_employer"] == 0 and amounts(no_eps)["pf_employer"] == 1800
    assert "Not a member of the pension scheme" in next(l for l in no_eps["lines"] if l["code"] == "pf_employer")["reason"]
    old = run(emp("A", 60000, date_of_birth="1966-04-10"), "2025-06", statutory_versions)  # 59 in June 2025
    assert amounts(old)["eps_employer"] == 0 and "stops at 58" in next(l for l in old["lines"] if l["code"] == "pf_employer")["reason"]
    young = amounts(run(emp("A", 60000, date_of_birth="1990-04-10"), "2025-06", statutory_versions))
    assert young["eps_employer"] == 1250
    higher = amounts(run(emp("A", 60000, pf_full_wage=True, eps_full_wage=True), "2025-06", statutory_versions))
    assert higher["eps_employer"] == Decimal("2499")                            # 8.33% of ₹30,000, rounded


# --------------------------------------------------------------------------- through a month

@pytest.mark.django_db
def test_a_missing_pan_reaches_the_months_tds(demo):
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots
    from payroll.features.payroll_runs import runs
    from payroll.models import DataSnapshot

    company, snapshot = demo
    maker = setup.system_user("maker")
    data = dict(snapshot.data)
    top = max(data["compensation"], key=lambda r: sum(Decimal(v) for v in r["components"].values()))["employee_code"]
    data["employees"] = [dict(e, pan="ABCDE1234F") if e["code"] != top else e for e in data["employees"]]
    data["fields_present"] = ["pan"]
    s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                    content_hash="with-pan", checks=[])
    snapshots.approve(s, maker)
    april = runs.open_month(company, "2025-04", maker, snapshot=s)
    runs.review_data(april.pk, maker)
    april = runs.calculate(april.pk, maker)
    people = {e["code"]: e for e in april.input["employees"]}
    assert people[top].get("no_pan") is True and not any(p.get("no_pan") for c, p in people.items() if c != top)
    tds = next(l for l in next(e for e in april.result["employees"] if e["code"] == top)["lines"] if l["code"] == "tds")
    assert "206AA" in tds["reason"]
