"""Run every answer-sheet case in answer_sheets/ (A, B, and C when it is added) and require every number to match."""
from pathlib import Path

import pytest

from payroll.demo.answer_sheet import format_report, run_case

ROOT = Path(__file__).resolve().parents[2] / "answer_sheets"
CASES = sorted(p for p in ROOT.iterdir() if (p / "choices.json").exists() and (p / "expected.csv").exists())


@pytest.mark.django_db(transaction=False)
@pytest.mark.parametrize("folder", CASES, ids=[p.name for p in CASES])
def test_answer_sheet(folder):
    report = run_case(folder, log=lambda *a: None)
    print(format_report(report))
    mismatches = [r for r in report["rows"] if r["status"] != "match"]
    assert not mismatches, format_report(report)


def _case_a_choices():
    import json
    return json.loads((ROOT / "case_A" / "choices.json").read_text())


def test_case_given_as_csv_only_builds_choices_from_another_case():
    from payroll.demo.answer_sheet import build_choices

    choices = build_choices(_case_a_choices(), ["lop.divisor=26", "pf_policy.wage_base=full"], "C")
    assert choices["case"] == "C"
    assert choices["settings"]["lop"]["divisor"] == 26
    assert choices["settings"]["pf_policy"]["wage_base"] == "full"
    assert choices["settings"]["pf_policy"]["employer_wage_base"] == "same"  # untouched fields are kept
    assert choices["paid_months"] == ["2025-04", "2025-05", "2025-06"]


@pytest.mark.parametrize("override, message", [
    ("lop.devisor=26", "unknown field 'devisor'"),
    ("lop.divisor=working_days", "'divisor' must be"),
    ("pf_policy.wage_base=half", "'wage_base' must be one of"),
    ("leave.divisor=26", "unknown setting 'leave'"),
    ("lop=26", "setting.field=value"),
])
def test_unsupported_case_choices_stop_before_running(override, message):
    from payroll.demo.answer_sheet import build_choices

    with pytest.raises(ValueError, match=message):
        build_choices(_case_a_choices(), [override])
