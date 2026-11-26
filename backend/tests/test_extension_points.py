"""The three places built to be extended without changing existing code: a step of the calculation, a kind of
change, and an AI provider. Each test adds one and checks that the rest of the system picks it up."""
from decimal import Decimal

import pytest

from payroll_engine.explain import DEDUCTION, Line
from payroll_engine.periods import Period
from payroll_engine.steps import STEPS, income_tax

from .test_engine import emp


def test_a_new_deduction_is_one_more_step(statutory_versions):
    from payroll_engine.engine import calculate_employee

    def labour_welfare_fund(c):
        c.lines.append(Line(code="lwf", label="Labour welfare fund", amount=Decimal(25), category=DEDUCTION, formula="₹25"))
        c.amounts["lwf"] = Decimal(25)
        c.add_deduction("labour welfare fund", Decimal(25))

    def lwf_company_share(c):
        c.amounts["lwf_employer"] = Decimal(75)
        c.add_employer_contribution("labour welfare fund", Decimal(75))

    at = STEPS.index(income_tax) + 1
    steps = STEPS[:at] + (labour_welfare_fund,) + STEPS[at:-1] + (lwf_company_share, STEPS[-1])
    e, period = emp("MEERA", 30000), Period.parse("2025-06")
    before = calculate_employee(e, period, statutory_versions, {"state": "KA"})
    after = calculate_employee(e, period, statutory_versions, {"state": "KA"}, steps=steps)
    a, b = before["amounts"], after["amounts"]
    assert Decimal(b["total_deductions"]) == Decimal(a["total_deductions"]) + 25
    assert Decimal(b["net_pay"]) == Decimal(a["net_pay"]) - 25
    assert Decimal(b["employer_cost"]) == Decimal(a["employer_cost"]) + 75
    lines = {line["code"]: line for line in after["lines"]}
    assert lines["total_deductions"]["reason"] == "PF + ESI + professional tax + TDS + labour welfare fund"
    assert lines["employer_cost"]["reason"] == "Gross pay plus the company's own PF, ESI and labour welfare fund"
    codes = [line["code"] for line in after["lines"]]
    assert codes.index("tds") < codes.index("lwf") < codes.index("total_deductions")
    # without the new steps nothing changes
    assert [line["code"] for line in before["lines"]] == [c for c in codes if c != "lwf"]


@pytest.mark.django_db
def test_a_new_kind_of_change_is_one_more_class(demo, monkeypatch):
    from payroll.demo import setup
    from payroll.features.payroll_runs import change_kinds, changes, runs
    from payroll.features.payroll_runs.inputs import build_run_input

    class ShiftAllowance(change_kinds.Kind):
        """A fixed night-shift allowance for the month, entered in the form."""
        type = "shift_allowance"

        def from_form(self, f, item):
            item.update({"item": "other_earning", "label": "Night-shift allowance", "amount": "1500", "taxable": True})
            item["summary"] = f"Pay the night-shift allowance of ₹1,500 to {f.who} in {f.period.label}."
            return f"Night-shift allowance: {f.emp['name']}"

        def apply(self, item, month, change):
            month.one_time.append({"ref": f"change:{change.pk}", "kind": "earning", "code": "shift_allowance",
                                   "label": item["label"], "amount": item["amount"], "taxable": True, "source_text": change.text})

    monkeypatch.setitem(change_kinds.KINDS, "shift_allowance", ShiftAllowance())
    company, _ = demo
    maker = setup.system_user("maker")
    july = runs.open_month(company, "2025-07", maker)
    ch = changes.add_by_hand(july, maker, {"kind": "shift_allowance", "employee_code": "BL002"})
    assert ch.proposal["items"][0]["summary"] == "Pay the night-shift allowance of ₹1,500 to Arjun Mehta (BL002) in July 2025."
    arjun = next(e for e in build_run_input(july)["employees"] if e["code"] == "BL002")
    assert arjun["one_time"][0]["amount"] == "1500" and arjun["applied_changes"] == [f"change:{ch.pk}"]
    # an unknown kind is still refused, and the built-in kinds are all there
    with pytest.raises(changes.RuleViolation):
        changes.add_by_hand(july, maker, {"kind": "no_such_kind", "employee_code": "BL002"})
    assert {"one_time_earning", "one_time_deduction", "unpaid_days", "extra_unpaid_days", "past_unpaid_days",
            "salary_revision"} <= set(change_kinds.KINDS)


@pytest.mark.django_db
def test_a_new_ai_provider_is_one_more_class(monkeypatch, settings):
    from payroll.features.ai import client

    class CompanyServerModel(client.ModelProvider):
        key = "own-server"

        def available(self):
            return True

        def model_name(self):
            return "a model on the company's own servers"

        def call(self, system, prompt, schema):
            return {"answer": "from own server"}, {"model_served": "own-model-1"}

    monkeypatch.setitem(client.PROVIDERS, "own-server", CompanyServerModel())
    settings.AI_PROVIDER = "own-server"
    assert client.provider() == "own-server" and client.is_model()
    assert client.describe()["model"] == "a model on the company's own servers"
    i = client.ask(purpose="field_mapping", system="s", prompt="p", schema={"type": "object"}, fallback=lambda: {"answer": "rules"})
    assert (i.provider, i.model, i.response) == ("own-server", "own-model-1", {"answer": "from own server"})
    settings.AI_PROVIDER = "off"
    assert client.provider() == client.RULE_BASED
