"""The files people upload to the bank, EPFO and ESIC, made when the HR data has the details they need: the bank
transfer file (and the people paid otherwise), the PF ECR upload and the ESI monthly return. They are read back and
totalled against the register like every other file; someone missing a UAN or an IP number is listed, not lost."""
import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings

from payroll.demo import setup
from payroll.features.hr_data import snapshots
from payroll.features.payroll_runs import runs
from payroll.models import DataSnapshot

pytestmark = pytest.mark.django_db
CASH, NO_UAN, LEAVER = "BL003", "BL004", "BL002"  # Arjun (BL002) is covered by ESI and leaves on 15 June


def _june(company, snapshot, details=True):
    for m in ("2025-04", "2025-05"):
        setup.run_month(company, m, log=lambda *a: None)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    data = dict(snapshot.data)
    people = []
    for n, e in enumerate(data["employees"]):
        e = dict(e)
        if details:
            e.update(bank_account=f"00{n:010d}", ifsc="HDFC0001234", payment_mode="cash" if e["code"] == CASH else "bank",
                     esi_number=f"{n:010d}" if n % 7 else None, uan=None if e["code"] == NO_UAN else f"10{n:010d}")
            e = {k: v for k, v in e.items() if v is not None}
        if e["code"] == LEAVER:
            e.update(date_of_exit="2025-06-15", exit_reason="C")
        people.append(e)
    data["employees"] = people
    if details:
        data["fields_present"] = ["bank_account", "ifsc", "payment_mode", "uan", "esi_number"]
    s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                    content_hash=f"filing-{details}", checks=[])
    snapshots.approve(s, maker)
    june = runs.open_month(company, "2025-06", maker, snapshot=s)
    runs.review_data(june.pk, maker)
    runs.calculate(june.pk, maker)
    runs.submit(june.pk, maker)
    runs.approve(june.pk, checker)
    return runs.finalize(june.pk, checker), data


def _read(run, name):
    return (Path(settings.MEDIA_ROOT) / run.files["folder"] / name).read_text()


def _rows(run, name):
    return list(csv.DictReader(io.StringIO(_read(run, name))))


def test_bank_ecr_and_esi_files_match_the_register(demo):
    company, snapshot = demo
    june, data = _june(company, snapshot)
    assert {"bank_transfer.csv", "other_payments.csv", "pf_ecr.txt", "pf_ecr_missing_uan.csv", "esi_return.csv",
            "esi_return_missing_ip.csv"} <= set(june.files["files"])
    checks = {c["check"]: c for c in june.files["reconciliation"]}
    assert all(c["matches"] for c in checks.values())
    assert "Net pay: bank transfers + other payments" in checks and "PF ECR: employee share (+ members without a UAN)" in checks
    result = {e["code"]: e for e in june.result["employees"]}

    # Bank: everyone paid by transfer, with their account and IFSC; the cash payee is listed apart.
    bank = {r["Employee ID"]: r for r in _rows(june, "bank_transfer.csv")}
    other = {r["Employee ID"]: r for r in _rows(june, "other_payments.csv")}
    assert CASH not in bank and other[CASH]["Paid by"] == "Cash"
    some = next(iter(bank.values()))
    assert some["IFSC"] == "HDFC0001234" and some["Narration"] == "Salary June 2025"
    assert Decimal(some["Amount"]) == Decimal(result[some["Employee ID"]]["amounts"]["net_pay"])
    assert set(bank) | set(other) == set(result)

    # PF ECR: one line per member with a UAN, 11 fields separated by #~#, UAN first, whole rupees.
    lines = [x.split("#~#") for x in _read(june, "pf_ecr.txt").splitlines()]
    assert lines and all(len(x) == 11 for x in lines) and all(len(x[0]) == 12 and x[0].isdigit() for x in lines)
    by_name = {x[1]: x for x in lines}
    priya = result["BL001"]
    line = by_name[priya["name"]]
    assert line[6] == str(int(Decimal(priya["amounts"]["pf_employee"]))) and line[7] == str(int(Decimal(priya["amounts"]["eps_employer"])))
    assert [r["Employee ID"] for r in _rows(june, "pf_ecr_missing_uan.csv")] == [NO_UAN]  # listed, not lost

    # ESI return: the people covered, days paid, wages; the leaver with reason 2 (left service) and the last day.
    esi = {r["IP Name"]: r for r in _rows(june, "esi_return.csv")}
    arjun = esi[result[LEAVER]["name"]]
    assert arjun["Reason Code for Zero workings days"] == "2" and arjun["Last Working Day"] == "15/06/2025"
    assert int(arjun["No of Days for which wages paid/payable during the month"]) <= 15
    covered = {c for c, e in result.items() if e["facts"].get("esi_covered")}
    no_ip = {e["code"] for e in data["employees"] if not e.get("esi_number")} & covered
    assert {r["Employee ID"] for r in _rows(june, "esi_return_missing_ip.csv")} == no_ip
    assert len(esi) + len(no_ip) == len(covered)


def test_without_these_details_the_files_are_as_before(demo):
    company, snapshot = demo
    june, _ = _june(company, snapshot, details=False)
    assert set(june.files["files"]) == {"register.csv", "pf.csv", "esi.csv", "professional_tax.csv", "tds.csv"}
