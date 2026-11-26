"""The calculation on its own: pure inputs in, numbers with reasons out. Values from the H-02 briefing."""
from decimal import Decimal

import pytest

from payroll_engine import policies as P
from payroll_engine.engine import calculate_employee, calculate_month, contracted_rate, select_pt_slab
from payroll_engine.hashing import sha256
from payroll_engine.money import inr, round_amount
from payroll_engine.periods import Period
from payroll_engine.tax import annual_tax

from .conftest import company_setting


def split(total):
    return {"basic": str(total * 50 // 100), "hra": str(total * 20 // 100), "special_allowance": str(total * 30 // 100)}


def emp(code, total, **kw):
    base = {"code": code, "name": code, "work_state": "KA", "compensation": [{"effective_from": "2025-04-01", "components": split(total)}],
            "unpaid_days": "0", "one_time": [], "ytd": {"months": []}, "arrears_basis": []}
    base.update(kw)
    return base


def run(e, period, versions):
    return calculate_employee(e, Period.parse(period), versions, {"state": "KA"})


def amounts(r):
    return {k: Decimal(v) for k, v in r["amounts"].items()}


def test_meera_normal_month(statutory_versions):
    a = amounts(run(emp("MEERA", 30000), "2025-06", statutory_versions))
    assert (a["pf_employee"], a["esi_employee"], a["pt"], a["tds"], a["net_pay"]) == (1800, 0, 200, 0, 28000)


def test_arjun_esi_case(statutory_versions):
    a = amounts(run(emp("ARJUN", 18000), "2025-06", statutory_versions))
    assert (a["pf_employee"], a["esi_employee"], a["esi_employer"], a["pt"], a["net_pay"]) == (1080, 135, 585, 0, 16785)


def test_kavya_tds_case(statutory_versions):
    a = amounts(run(emp("KAVYA", 150000), "2025-04", statutory_versions))
    assert a["tds_annual_tax"] == 150800
    assert a["tds"] == 12567
    assert a["net_pay"] == 135433


def test_arjun_three_unpaid_days_in_july_days_in_month(statutory_versions):
    a = amounts(run(emp("ARJUN", 18000, unpaid_days="3"), "2025-07", statutory_versions))
    assert a["lop_deduction"] == 1742
    assert a["gross"] == 16258
    assert a["pf_employee"] == 975
    assert a["esi_employee"] == 122
    assert a["net_pay"] == 15161


@pytest.mark.parametrize("divisor,earnings_rounding,cut", [
    (30, "per_component", 1800),
    (26, "per_component", 2076),  # 1,038 + 415 + 623: each part rounded
    (26, "gross_only", 2077),  # 2,076.92 rounded once
])
def test_lop_divisor_is_a_company_setting(statutory_versions, divisor, earnings_rounding, cut):
    versions = statutory_versions + [company_setting("lop", {"divisor": divisor}),
                                     company_setting("rounding", {"method": "nearest", "earnings": earnings_rounding})]
    a = amounts(run(emp("ARJUN", 18000, unpaid_days="3"), "2025-07", versions))
    assert a["lop_deduction"] == cut


def test_pf_full_basic_setting(statutory_versions):
    versions = statutory_versions + [company_setting("pf_policy", {"wage_base": "full"})]
    a = amounts(run(emp("KAVYA", 150000), "2025-06", versions))
    assert a["pf_employee"] == 9000
    assert a["eps_employer"] == 1250  # pension part stays on the ₹15,000 ceiling


def test_priya_arrears_bonus_july(statutory_versions):
    paid = {"earned": split(80000), "pf_employee": "1800", "pf_employer": "1800"}
    e = emp("PRIYA", 80000,
            compensation=[{"effective_from": "2025-04-01", "components": split(80000)}, {"effective_from": "2025-05-01", "components": split(90000)}],
            one_time=[{"ref": "t", "kind": "earning", "code": "bonus", "label": "Bonus", "amount": "10000", "taxable": True}],
            ytd={"months": [{"period": p, "gross": "80000", "tds": "0"} for p in ("2025-04", "2025-05", "2025-06")]},
            arrears_basis=[{"period": p, "policy_versions": statutory_versions, "unpaid_days": "0", "paid": paid} for p in ("2025-05", "2025-06")])
    r = run(e, "2025-07", statutory_versions)
    a = amounts(r)
    assert (a["arrears"], a["gross"], a["pf_employee"], a["tds"], a["net_pay"]) == (20000, 120000, 1800, 0, 118000)
    assert a["tds_taxable_income"] == 1005000  # "about ₹10.05 lakh" in the briefing
    arrears = next(line for line in r["lines"] if line["code"] == "arrears")
    assert [c["label"] for c in arrears["children"]] == ["Arrears for May 2025", "Arrears for June 2025"]


def test_arrears_already_paid_are_not_paid_twice(statutory_versions):
    # May was paid at 80k, then 10k arrears for May were paid later: nothing more is owed.
    paid = {"earned": split(90000), "pf_employee": "1800", "pf_employer": "1800"}
    e = emp("PRIYA", 90000, compensation=[{"effective_from": "2025-04-01", "components": split(80000)},
                                         {"effective_from": "2025-05-01", "components": split(90000)}],
            arrears_basis=[{"period": "2025-05", "policy_versions": statutory_versions, "unpaid_days": "0", "paid": paid}])
    assert amounts(run(e, "2025-08", statutory_versions))["arrears"] == 0


def test_every_number_has_a_reason(statutory_versions):
    r = run(emp("ARJUN", 18000, unpaid_days="3"), "2025-07", statutory_versions)
    for line in r["lines"]:
        assert line["formula"] or line["reason"], line["code"]
    pf = next(line for line in r["lines"] if line["code"] == "pf_employee")
    assert pf["formula"] == "12% of ₹8,129 = ₹975"
    assert {ref["key"] for ref in pf["rules"]} >= {"pf", "pf_policy"}


def test_same_input_same_result(statutory_versions):
    month = {"period": "2025-07", "company": {"state": "KA"}, "policy_versions": statutory_versions,
             "employees": [emp("A", 18000, unpaid_days="3"), emp("B", 150000)]}
    assert sha256(calculate_month(month)) == sha256(calculate_month(month))


def test_rule_versions_picked_by_date(statutory_versions):
    new_pt = {"id": "99", "key": "professional_tax", "scope": "statutory", "jurisdiction": "IN-KA", "effective_from": "2025-08-01",
              "payload": {"slabs": [{"min": "0", "amount": "0"}, {"min": "20000", "amount": "250"}]}, "title": "new"}
    versions = statutory_versions + [new_pt]
    assert amounts(run(emp("SNEHA", 25000), "2025-07", versions))["pt"] == 200  # before the start date: unchanged
    assert amounts(run(emp("SNEHA", 25000), "2025-08", versions))["pt"] == 250
    assert amounts(run(emp("RAVI", 21000), "2025-08", versions))["pt"] == 250


def test_mid_month_rule_starts_next_month(statutory_versions):
    v = {"id": "x", "key": "esi", "scope": "statutory", "jurisdiction": "IN", "effective_from": "2025-07-15",
         "payload": {"threshold": "25000", "employee_rate": "0.0075", "employer_rate": "0.0325"}}
    assert P.resolve(statutory_versions + [v], "esi", Period(2025, 7), "IN").ref.version_id != "x"
    assert P.resolve(statutory_versions + [v], "esi", Period(2025, 8), "IN").ref.version_id == "x"


def test_esi_boundary_and_round_up(statutory_versions):
    a = amounts(run(emp("RAVI", 21000), "2025-07", statutory_versions))
    assert a["esi_employee"] == 158 and a["esi_employer"] == 683  # 157.5 and 682.5 rounded up
    assert amounts(run(emp("X", 21100), "2025-07", statutory_versions))["esi_employee"] == 0


def test_pt_slabs_gender_and_february(statutory_versions):
    mh = next(v for v in statutory_versions if v["jurisdiction"] == "IN-MH")["payload"]["slabs"]
    assert select_pt_slab(mh, Decimal(9000), 6, "M")["amount"] == "175"
    assert select_pt_slab(mh, Decimal(30000), 2, "M")["amount"] == "300"
    assert select_pt_slab(mh, Decimal(20000), 6, "F")["amount"] == "0"
    assert select_pt_slab(mh, Decimal(30000), 6, "F")["amount"] == "200"


def test_marginal_relief_above_12_lakh(statutory_versions):
    rule = next(v for v in statutory_versions if v["key"] == "income_tax")["payload"]
    assert annual_tax(Decimal(1200000), rule).total == 0
    # ₹12,10,000: slab tax 61,500 but relief caps it at the ₹10,000 above 12 lakh, plus 4% cess
    assert annual_tax(Decimal(1210000), rule).total == 10400


def test_mid_month_salary_change_is_weighted_by_days():
    rates, note, _ = contracted_rate([{"effective_from": "2025-04-01", "components": {"basic": "31000"}},
                                      {"effective_from": "2025-07-17", "components": {"basic": "62000"}}], Period(2025, 7))
    assert rates["basic"] == Decimal(31000) * 16 / 31 + Decimal(62000) * 15 / 31
    assert "changed during the month" in note


def test_joiner_mid_month_paid_for_days_worked(statutory_versions):
    a = amounts(run(emp("NEW", 31000, date_of_joining="2025-07-17"), "2025-07", statutory_versions))
    assert a["gross"] == 15000  # 15 of 31 days


def test_labour_code_rule_adds_excess_to_pf_wages(statutory_versions):
    comp = {"basic": "24000", "hra": "12000", "special_allowance": "24000"}
    versions = statutory_versions + [company_setting("pf_policy", {"wage_base": "full"})]
    e = emp("NISHA", 0, compensation=[{"effective_from": "2025-04-01", "components": comp}])
    assert amounts(run(e, "2025-11", versions))["pf_employee"] == 2880  # rule starts 21 Nov: from December
    assert amounts(run(e, "2025-12", versions))["pf_employee"] == 3600  # 12% of 50% of 60,000


def test_company_on_older_labour_laws_skips_the_50_percent_rule(statutory_versions):
    comp = {"basic": "24000", "hra": "12000", "special_allowance": "24000"}
    versions = statutory_versions + [company_setting("pf_policy", {"wage_base": "full"}),
                                     company_setting("labour_codes", {"apply": "not_applied"})]
    e = emp("NISHA", 0, compensation=[{"effective_from": "2025-04-01", "components": comp}])
    r = run(e, "2025-12", versions)
    assert amounts(r)["pf_employee"] == 2880  # 12% of basic only
    assert P.validate_payload("labour_codes", {"apply": "maybe"})


def test_rounding_choices():
    assert round_amount(Decimal("975.5")) == 976
    assert round_amount(Decimal("121.01"), "up") == 122
    assert round_amount(Decimal("12566.5"), "nearest_even") == 12566
    assert inr(Decimal("135433")) == "₹1,35,433"


def test_validation_rejects_bad_rule_data():
    assert P.validate_payload("lop", {"divisor": 0})
    assert P.validate_payload("professional_tax", {"slabs": [{"min": 0}]})
    assert not P.validate_payload("professional_tax", {"slabs": [{"min": 0, "amount": 0}, {"above": "15000", "amount": 150}]})


def test_no_arrears_against_a_month_whose_salary_is_missing(statutory_versions):
    """If HR data only has the new salary (from May), April cannot be recalculated: warn, never guess."""
    paid = {"earned": split(80000), "pf_employee": "1800", "pf_employer": "1800"}
    e = emp("PRIYA", 0, compensation=[{"effective_from": "2025-05-01", "components": split(90000)}],
            arrears_basis=[{"period": p, "policy_versions": statutory_versions, "unpaid_days": "0", "paid": paid}
                           for p in ("2025-04", "2025-05", "2025-06")])
    r = run(e, "2025-07", statutory_versions)
    assert amounts(r)["arrears"] == 20000  # May and June only
    assert any("No salary on record for April 2025" in i["message"] for i in r["issues"])


def test_someone_joining_mid_month_gets_no_missing_salary_note(statutory_versions):
    """Days before joining are not paid, so a salary that starts on the joining date covers the month."""
    from datetime import date

    from payroll_engine.periods import Period

    pay = [{"effective_from": "2025-05-05", "components": split(40000)}]
    _, _, backfilled = contracted_rate(pay, Period.parse("2025-05"), date(2025, 5, 5))
    assert backfilled is False
    # A salary that starts after the joining date leaves days without a salary: that is still pointed out.
    _, _, backfilled = contracted_rate(pay, Period.parse("2025-05"), date(2025, 5, 1))
    assert backfilled is True
    e = emp("NJ1", 40000, date_of_joining="2025-05-05")
    e["compensation"] = pay
    r = run(e, "2025-05", statutory_versions)
    assert not [i for i in r["issues"] if "starts after the month began" in i["message"]]
    assert r["amounts"]["gross"] == "34839"  # 27 of 31 days of 40,000


def test_raise_and_leaving_in_the_same_month_cut_each_part_at_its_own_salary(statutory_versions):
    """A raise on 16 July and the last working day on 20 July: 15 days at ₹30,000 and 5 days at ₹60,000 (₹24,194).
    Cutting the 11 days after leaving at the month's blended rate paid ₹29,345 (as the PayrollEngine framework splits
    time-bound values by period, each part of the month now keeps its own salary)."""
    e = emp("EXIT", 30000, date_of_exit="2025-07-20",
            compensation=[{"effective_from": "2025-04-01", "components": split(30000)},
                          {"effective_from": "2025-07-16", "components": split(60000)}])
    r = run(e, "2025-07", statutory_versions)
    exact = Decimal(30000) * 15 / 31 + Decimal(60000) * 5 / 31
    assert abs(Decimal(r["amounts"]["regular_gross"]) - exact) <= 2  # each part of pay rounded to the rupee
    basic = next(line for line in r["lines"] if line["code"] == "earned.basic")
    assert "each cut at the salary in force on that day" in basic["formula"]

    # A raise without a leaving date, or a leaving date without a raise, is worked out exactly as before.
    def as_before(rates, unpaid):  # each part of pay: monthly − monthly ÷ 31 × unpaid days, each rounded to the rupee
        return sum(round_amount(Decimal(v)) - round_amount(Decimal(v) / 31 * unpaid) for v in rates.values())

    stays = run({**e, "date_of_exit": None, "unpaid_days": "2"}, "2025-07", statutory_versions)
    blended = {k: Decimal(split(30000)[k]) * 15 / 31 + Decimal(split(60000)[k]) * 16 / 31 for k in split(30000)}
    assert Decimal(stays["amounts"]["regular_gross"]) == as_before(blended, 2)
    one_salary = run({**e, "compensation": e["compensation"][:1]}, "2025-07", statutory_versions)
    assert Decimal(one_salary["amounts"]["regular_gross"]) == as_before(split(30000), 11)


def test_the_leaving_month_counts_only_the_days_worked_in_the_tax_estimate(statutory_versions):
    """TDS estimates the rest of the year at the current salary; the month of leaving pays only up to the last day."""
    leaves = run(emp("LEAVER", 100000, date_of_exit="2025-09-15"), "2025-07", statutory_versions)
    stays = run(emp("STAYER", 100000), "2025-07", statutory_versions)
    # July + August + 15 of 30 days of September, against July to March
    assert Decimal(leaves["amounts"]["tds_projected_income"]) == 100000 + 100000 + 50000
    assert Decimal(stays["amounts"]["tds_projected_income"]) == 100000 * 9


@pytest.mark.parametrize("state, gross, expected", [
    ("TG", 15000, 0), ("TG", 18000, 150), ("TG", 30000, 200),
    ("AP", 15000, 0), ("AP", 20000, 150), ("AP", 20100, 200),
    ("GJ", 12000, 0), ("GJ", 12100, 200),
    ("AS", 15000, 0), ("AS", 20000, 180), ("AS", 25000, 208),
    ("OR", 13304, 0), ("OR", 20000, 125), ("OR", 30000, 200),
    ("SK", 20000, 0), ("SK", 25000, 125), ("SK", 35000, 150), ("SK", 50000, 200),
    ("TR", 7500, 0), ("TR", 10000, 150), ("TR", 20000, 208),
    ("DL", 90000, 0), ("HR", 90000, 0), ("UP", 90000, 0), ("UT", 90000, 0),
])
def test_each_state_pays_its_own_professional_tax(statutory_versions, state, gross, expected):
    """The state's slab applies by itself: PT follows the employee's work location (by law, where they work)."""
    r = run(emp("E", gross, work_state=state), "2025-07", statutory_versions)
    assert Decimal(r["amounts"]["pt"]) == expected
    line = next(x for x in r["lines"] if x["code"] == "pt")
    assert "work location" in line["reason"]
    if state in ("DL", "HR", "UP", "UT"):
        assert "does not charge professional tax" in line["reason"] and not r["issues"]


def test_odisha_charges_more_in_february(statutory_versions):
    assert Decimal(run(emp("E", 30000, work_state="OR"), "2026-02", statutory_versions)["amounts"]["pt"]) == 300
    assert Decimal(run(emp("E", 30000, work_state="OR"), "2026-01", statutory_versions)["amounts"]["pt"]) == 200


def test_no_work_location_uses_the_company_state_and_says_so(statutory_versions):
    e = emp("E", 30000, work_state="")
    r = calculate_employee(e, Period.parse("2025-07"), statutory_versions, {"state": "TG"})
    line = next(x for x in r["lines"] if x["code"] == "pt")
    assert Decimal(r["amounts"]["pt"]) == 200 and "company's state" in line["reason"]


def test_months_before_a_state_rule_starts_stay_as_they_were(statutory_versions):
    """The new state rules start on 1 April 2025: a month before that finds no Telangana rule (PT ₹0, as before),
    and Karnataka and Maharashtra still resolve to their own, unchanged rules."""
    assert P.resolve(statutory_versions, "professional_tax", Period.parse("2025-03"), "IN-TG") is None
    assert P.resolve(statutory_versions, "professional_tax", Period.parse("2025-04"), "IN-TG") is not None
    for state, start in (("IN-KA", "2023-04-01"), ("IN-MH", "2023-04-01")):
        assert P.resolve(statutory_versions, "professional_tax", Period.parse("2025-07"), state).ref.effective_from == start


def test_a_state_without_professional_tax_is_valid_data_without_slabs():
    assert P.validate_payload("professional_tax", {"levied": False, "state_name": "Delhi"}) == []
    assert P.validate_payload("professional_tax", {"levied": False, "slabs": [{"min": "0", "amount": "200"}]})
    assert P.validate_payload("professional_tax", {"levied": "no"})
