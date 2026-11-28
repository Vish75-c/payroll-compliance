"""Reading any HR tool's export, not one template: a sheet that declares a wrong size, lines of text under the data,
unpaid days with no month column, and every column that does not feed pay shown. The files here are made in the
test with the same problems as real exports (and the real templates are checked too when their folder is present)."""
import io
import re
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from payroll.features.hr_data.spreadsheet import _is_note, read_spreadsheet
from payroll.models import DataSnapshot

from .conftest import REPO
from .test_api import _sheet_file, login

JSON = "application/json"
NOTES = [[None, "Notes:", "Columns in red are mandatory"],
         [None, None, "Please ensure the columns contain data in the proper format. For example, date values should be correct."]]


def _xlsx(rows, declared_size=None) -> bytes:
    """A workbook with ``rows``. ``declared_size`` writes a wrong sheet size into the file, as some exports do."""
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    if declared_size is None:
        return buf.getvalue()
    src, out = zipfile.ZipFile(io.BytesIO(buf.getvalue())), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                tag = f'<dimension ref="{declared_size}"/>'.encode()
                data = (re.sub(rb'<dimension ref="[^"]*"\s*/>', tag, data) if b"<dimension" in data
                        else data.replace(b"<sheetData", tag + b"<sheetData", 1))
            dst.writestr(item, data)
    return out.getvalue()


# --------------------------------------------------------------------------- the reader

def test_a_sheet_that_declares_a_wrong_size_is_read_whole():
    rows = [["Employee Number", "LOP Days", "Remarks"], ["E0001", "1.00", ""], ["E0002", "2", "half month"]]
    content = _xlsx(rows, declared_size="A1")
    assert b'<dimension ref="A1"/>' in zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml")
    [table] = read_spreadsheet("Employee LOP.xlsx", content)
    assert table["headers"] == ["Employee Number", "LOP Days", "Remarks"]
    assert [r["Employee Number"] for r in table["rows"]] == ["E0001", "E0002"]


def test_lines_of_text_under_the_data_are_not_people():
    rows = [["Employee Number", "Name", "Joining Date"], ["E0001", "Kumar Anand Rao", "01-May-2005"],
            ["E0002", "Asha Nair", "02-Jun-2019"], *NOTES]
    [table] = read_spreadsheet("people.xlsx", _xlsx(rows, declared_size="A1"))
    assert [r["Employee Number"] for r in table["rows"]] == ["E0001", "E0002"]
    assert table["read_info"]["notes_skipped"][0] == "Notes: Columns in red are mandatory"


@pytest.mark.parametrize("row, note", [
    ({"a": "Notes:", "b": "Columns in red are mandatory"}, True),
    ({"a": "", "b": "Please ensure the columns contain data in the proper format."}, True),
    ({"a": "E0001", "b": "on leave for three days"}, False),   # an employee ID: data
    ({"a": "Leave total", "b": "12"}, False),                  # a number: data
    ({"a": "Asha Nair", "b": "01-May-2005"}, False),           # a date: data
    ({"a": "Asha Nair", "b": ""}, False),                      # a name alone is not a sentence
])
def test_what_counts_as_a_note(row, note):
    assert _is_note(row) is note


def test_only_text_after_the_last_person_is_set_aside():
    rows = [["Employee Number", "Remarks"], ["E0001", "ok"], [None, "This line is between two people here"], ["E0002", "ok"]]
    [table] = read_spreadsheet("x.xlsx", _xlsx(rows))
    assert len(table["rows"]) == 3 and "notes_skipped" not in table["read_info"]  # in the middle it stays, and is reported later


# --------------------------------------------------------------------------- unpaid days without a month

def _upload(maker, company, lop: bytes):
    job = maker.post(f"/api/companies/{company.pk}/uploads", {
        "employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary"), "leave": _sheet_file("Leave"),
        "attendance": SimpleUploadedFile("Employee LOP.xlsx", lop)}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"],
                                                                     "value_maps": t["mapping"]["value_maps"]}, content_type=JSON)
            assert r.status_code == 200, r.content
    return maker.get(f"/api/jobs/{job['id']}").json()


def _lop():
    return _xlsx([["Employee Number", "LOP Days", "Remarks"], ["BL016", "2.00", ""], ["BL057", "1.00", "forgot to punch"], *NOTES],
                 declared_size="A1")


@pytest.mark.django_db
def test_unpaid_days_without_a_month_are_given_the_month_chosen_for_this_upload_only(demo):
    company, _ = demo
    maker = login("maker")
    job = _upload(maker, company, _lop())
    lop = next(t for t in job["tables"] if t["name"] == "Attendance")
    assert lop["mapping"]["mapping"] == {"Employee Number": "employee.code", "LOP Days": "attendance.unpaid_days", "Remarks": "ignore"}
    assert len(lop["rows"]) == 2 and len(lop["read_info"]["notes_skipped"]) == 2

    # No month: building is refused before anything is built, so the month can still be chosen on this upload.
    refused = maker.post(f"/api/jobs/{job['id']}/build-snapshot")
    assert refused.status_code == 400 and "Choose the payroll month of the unpaid days in Attendance" in refused.json()["detail"]
    assert not DataSnapshot.objects.filter(job_id=job["id"]).exists()
    assert maker.post(f"/api/tables/{lop['id']}/month", {"month": "July"}, content_type=JSON).status_code == 400
    assert login("checker").post(f"/api/tables/{lop['id']}/month", {"month": "2025-07"}, content_type=JSON).status_code == 403
    assert maker.post(f"/api/tables/{lop['id']}/month", {"month": "2025-07"}, content_type=JSON).status_code == 200

    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    data = DataSnapshot.objects.get(pk=snap["id"]).data
    assert {(a["employee_code"], a["period"], a["unpaid_days"]) for a in data["attendance"]} == {("BL016", "2025-07", "2"), ("BL057", "2025-07", "1")}
    checks = {c["code"]: c for c in snap["checks"]}
    assert "attendance_without_month" not in checks
    assert "Attendance: 1 of 3 columns are not used for pay: Remarks." in [c["message"] for c in snap["checks"] if c["code"] == "columns_not_used"]
    assert "Attendance: 2 lines of text under the data were not read as people" in checks["notes_skipped"]["message"]

    # Next month's file has the same columns: the confirmed mapping is reused, but July is not: the month is asked again.
    again = _upload(maker, company, _lop())
    lop2 = next(t for t in again["tables"] if t["name"] == "Attendance")
    assert lop2["mapping"]["confirmed"] and "month" not in lop2["read_info"]
    assert maker.post(f"/api/jobs/{again['id']}/build-snapshot").status_code == 400


def test_one_message_per_table_when_unpaid_days_have_no_month():
    from datetime import date

    from payroll.features.hr_data.normalize import build_snapshot

    structure = {"components": [{"code": "basic", "share": "remainder"}]}
    tables = [{"name": "LOP", "headers": ["Emp", "Days"], "rows": [{"Emp": "E1", "Days": "1"}, {"Emp": "E2", "Days": "2"}],
               "mapping": {"Emp": "employee.code", "Days": "attendance.unpaid_days"}}]
    _, checks = build_snapshot(tables, structure, date(2025, 7, 1))
    no_month = [c for c in checks if c["code"] == "attendance_without_month"]
    assert len(no_month) == 1 and "2 rows of unpaid days but no month" in no_month[0]["message"]


# --------------------------------------------------------------------------- the real templates, when present

FOLDER = REPO / "fwdpayrollcomplianceauthorisationforaccesstocompet"


@pytest.mark.skipif(not FOLDER.exists(), reason="the sample templates are not in this checkout")
@pytest.mark.parametrize("name", ["Add Employee Importer.xlsx", "Add_Revise Salary.xlsx", "Bulk Salary Information Of Employees.xlsx",
                                  "Employee LOP.xlsx", "Employee Resignation.xlsx"])
def test_the_sample_templates_are_read(name):
    [table] = read_spreadsheet(name, (FOLDER / name).read_bytes())
    assert len(table["rows"]) == 1 and table["rows"][0]["Employee Number"] == "E0001"
    assert table["read_info"]["notes_skipped"][0].startswith("Notes:")
