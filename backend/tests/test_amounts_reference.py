"""Every amount, for every employee, every month, against a second calculator written separately
(tests/reference_payroll.py), which reads the HR facts from the seed data rather than from what was imported."""
import json
from decimal import Decimal

import pytest

from payroll.demo import setup

from .conftest import REPO
from .reference_payroll import Reference, Rules, compare, people_from_seed

pytestmark = pytest.mark.django_db
MONTHS = ["2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10"]
BONUS = "Give Priya a ₹10,000 one-time bonus this month"


@pytest.mark.parametrize("day_counts", [
    {},                                    # the company setting: days in the month
    {m: 26 for m in MONTHS},               # 26 days every month
    {"2025-06": 30, "2025-07": 26},        # set differently for two months (arrears use each month's own)
], ids=["days-in-month", "26-every-month", "mixed"])
def test_brickline_april_to_october_matches_the_reference_calculator(demo, day_counts):
    company, _ = demo
    seed = json.loads((REPO / "data/brickline/seed.json").read_text())
    ref = Reference(people_from_seed(seed), Rules())
    priya = next(e["code"] for e in seed["employees"] if e["name"] == "Priya Sharma")
    problems, checked = [], 0
    for month in MONTHS:
        run = setup.run_month(company, month, [BONUS] if month == "2025-07" else None, log=lambda *a: None,
                              day_count=day_counts.get(month))
        want = ref.month(month, day_count=day_counts.get(month), one_time={priya: Decimal(10000)} if month == "2025-07" else None)
        problems += compare(run.result, want)
        checked += sum(len([k for k in row if not k.startswith("_")]) for row in want.values())
        ref.finalise(month, want)
        # the month's totals are the sum of its people
        for key in ("gross", "net_pay", "tds", "pf_employee", "employer_cost"):
            assert Decimal(run.result["totals"][key]) == sum((row[key] for row in want.values()), Decimal(0))
    assert not problems, f"{len(problems)} of {checked} amounts differ:\n" + "\n".join(problems[:40])
    assert checked > 7 * 80 * 15
    if not day_counts:
        # the cases the August-October data is there for really happened
        final = {e["code"]: e["amounts"] for e in run.result["employees"]}  # October
        assert "BL033" not in final and "BL058" not in final and "BL042" not in final and "BL061" in final
        assert "BL085" in final and final["BL085"]["not_employed_days"] == "5"   # joined 6 October
        assert final["BL061"]["not_employed_days"] == "21"                        # left 10 October
        assert final["BL079"]["arrears"] == "4000"                                # raise for September approved in October


def test_northwind_october_to_february_matches_the_reference_calculator(northwind):
    """A company that starts mid-year: opening balances in TDS, a fixed 30-day setting, Maharashtra professional tax
    (February's higher amount, women and men), the Labour Codes add-back from December, a raise approved late, a
    leave that spans two months, joiners in November and January."""
    import csv

    company, seed, _ = northwind
    with open(REPO / "data/northwind/opening_balances.csv") as f:
        openings = {r["Employee ID"]: (Decimal(r["Gross paid this year"]), Decimal(r["TDS this year"])) for r in csv.DictReader(f)}
    ref = Reference(people_from_seed(seed), Rules(), openings, company_day_count=30)
    problems, seen = [], {}
    for month in ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]:
        run = setup.run_month(company, month, log=lambda *a: None)
        want = ref.month(month, day_count=30)
        problems += compare(run.result, want)
        ref.finalise(month, want)
        seen[month] = {e["code"]: e["amounts"] for e in run.result["employees"]}
        # what is owed to the government is what the people's lines add up to
        owed = {(x.kind, x.jurisdiction): x.total for x in run.dues.all()}
        assert owed[("pt", "MH")] == sum((row["pt"] for row in want.values()), Decimal(0))
        assert owed[("tds", "")] == sum((row["tds"] for row in want.values()), Decimal(0))
    assert not problems, f"{len(problems)} amounts differ:\n" + "\n".join(problems[:40])
    # the cases this data is there for really happened
    assert seen["2025-10"]["NW001"]["tds_paid_before"] == "368940"
    assert seen["2026-02"]["NW002"]["pt"] == "300" and seen["2026-01"]["NW002"]["pt"] == "200"
    assert seen["2026-02"]["NW025"]["pt"] == "175" and seen["2026-02"]["NW019"]["pt"] == "0"  # men above 7,500; women up to 25,000
    assert Decimal(seen["2026-01"]["NW005"]["arrears"]) == 2 * 15000  # raise for Nov and Dec approved in January
    assert seen["2025-11"]["NW021"]["pf_wage"] == "10000" and seen["2025-12"]["NW021"]["pf_wage"] == "14000"  # Labour Codes add-back
    assert seen["2026-01"]["NW018"]["unpaid_days"] == "2.5" and seen["2026-02"]["NW018"]["unpaid_days"] == "2.5"
    assert "NW023" in seen["2025-11"] and "NW023" not in seen["2025-10"] and "NW024" in seen["2026-01"]
    assert Decimal(seen["2025-11"]["NW023"]["gross"]) == 15000 - sum(Decimal(round(Decimal(x) * 16 / 30)) for x in (7500, 3000, 4500))
