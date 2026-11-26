"""The worked examples of 'H-02: Payroll basics (read this first)', run through the whole app.

These numbers are copied from the organisers' document, not from answer_sheets/*.csv, so a mistake
in an expected CSV cannot hide a wrong result: cases A and B must also agree with the document.
"""
from functools import lru_cache
from pathlib import Path

import pytest

from payroll.demo.answer_sheet import run_case

ROOT = Path(__file__).resolve().parents[2] / "answer_sheets"

# (case, month, employee, item, amount in the document)
DOCUMENT = [
    # 3. A normal month, worked out (June)
    ("A", "2025-06", "Meera Iyer", "pf_employee", "1800"),      # 12% of ₹15,000
    ("A", "2025-06", "Meera Iyer", "esi_employee", "0"),        # ₹30,000 is above ₹21,000
    ("A", "2025-06", "Meera Iyer", "pt", "200"),                # ₹30,000 is ₹25,000 or more
    ("A", "2025-06", "Meera Iyer", "tds", "0"),
    ("A", "2025-06", "Meera Iyer", "net_pay", "28000"),
    ("A", "2025-06", "Arjun Mehta", "pf_employee", "1080"),     # 12% of ₹9,000
    ("A", "2025-06", "Arjun Mehta", "esi_employee", "135"),     # 0.75% of ₹18,000
    ("A", "2025-06", "Arjun Mehta", "esi_employer", "585"),     # 3.25% of ₹18,000
    ("A", "2025-06", "Arjun Mehta", "pt", "0"),
    ("A", "2025-06", "Arjun Mehta", "net_pay", "16785"),
    ("A", "2025-06", "Kavya Reddy", "tds", "12567"),            # ₹1,50,800 for the year ÷ 12
    ("A", "2025-06", "Kavya Reddy", "net_pay", "135433"),
    # 4. What changes do to a month (July)
    ("A", "2025-07", "Priya Sharma", "arrears", "20000"),       # ₹10,000 for each of May and June
    ("A", "2025-07", "Priya Sharma", "bonus", "10000"),
    ("A", "2025-07", "Priya Sharma", "gross", "120000"),
    ("A", "2025-07", "Priya Sharma", "pf_employee", "1800"),    # basic already above ₹15,000
    ("A", "2025-07", "Priya Sharma", "tds", "0"),               # about ₹10.05 lakh, no tax up to ₹12 lakh
    ("A", "2025-07", "Priya Sharma", "net_pay", "118000"),
    ("A", "2025-07", "Arjun Mehta", "lop_deduction", "1742"),   # ₹18,000 ÷ 31 × 3
    ("A", "2025-07", "Arjun Mehta", "gross", "16258"),
    ("A", "2025-07", "Arjun Mehta", "pf_employee", "975"),
    ("A", "2025-07", "Arjun Mehta", "esi_employee", "122"),
    ("A", "2025-07", "Arjun Mehta", "net_pay", "15161"),
    ("B", "2025-07", "Arjun Mehta", "lop_deduction", "1800"),   # "With '30 days' it would be ₹1,800"
]


@lru_cache(maxsize=None)
def _actual(case: str) -> dict:
    report = run_case(ROOT / f"case_{case}", log=lambda *a: None)
    return {(r["month"], r["employee"], r["item"]): r["actual"] for r in report["rows"]}


@pytest.mark.django_db(transaction=False)
@pytest.mark.parametrize("case, month, employee, item, amount", DOCUMENT,
                         ids=[f"{c}-{m}-{e.split()[0]}-{i}" for c, m, e, i, _ in DOCUMENT])
def test_payroll_basics_document(case, month, employee, item, amount):
    actual = _actual(case)
    key = (month, employee, item)
    assert key in actual, f"answer sheet {case} has no {item} for {employee} in {month}"
    assert actual[key] == amount, f"{employee} {month} {item}: document says {amount}, the app gives {actual[key]}"


def test_a_later_salary_record_for_the_same_start_date_is_a_correction():
    """Two records starting on the same day: the one recorded later wins, whatever order the HR data lists them in."""
    from datetime import date

    from payroll.features.payroll_runs.inputs import _known_compensation

    rows = [{"effective_from": "2025-07-01", "recorded_on": "2025-07-20", "components": {"basic": "52000"}},
            {"effective_from": "2025-07-01", "recorded_on": "2025-07-05", "components": {"basic": "50000"}},
            {"effective_from": "2025-04-01", "recorded_on": "2025-04-01", "components": {"basic": "45000"}}]
    known = _known_compensation(rows, date(2025, 7, 31))
    assert [r["components"]["basic"] for r in known] == ["45000", "50000", "52000"]  # the engine takes the last one
    assert [r["components"]["basic"] for r in _known_compensation(rows, date(2025, 7, 10))] == ["45000", "50000"]
