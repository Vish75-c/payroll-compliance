"""AI is only allowed in five places, and never touches a number."""
import ast
from pathlib import Path

import pytest

from payroll.features.ai import tasks
from payroll.models import ChangeRequest

ENGINE = Path(__file__).resolve().parents[1] / "payroll_engine"


def test_engine_imports_no_ai_no_database_no_network():
    banned = ("anthropic", "django", "payroll", "requests", "httpx", "playwright", "socket", "datetime.datetime.now")
    for path in ENGINE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(banned), f"{path.name} imports {name}"
        text = path.read_text()
        assert ".now(" not in text and "random" not in text, f"{path.name} must not use the clock or randomness"


def test_rule_based_parser_reads_the_demo_instruction():
    out = tasks.rule_based_instruction("Give Priya a ₹10,000 one-time bonus this month", "2025-07",
                                       [{"code": "BL001", "name": "Priya Sharma"}, {"code": "BL002", "name": "Arjun Mehta"}])
    change = out["changes"][0]
    assert change["type"] == "one_time_earning" and change["item"] == "bonus"
    assert change["amount_as_written"] == "₹10,000" and change["employee_as_written"] == "Priya"


@pytest.mark.django_db
def test_amount_not_written_in_the_instruction_is_refused(demo):
    from payroll.features.payroll_runs import changes, runs
    from payroll.demo import setup

    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-07", maker)
    proposal = changes.validate_proposal(
        {"changes": [{"type": "one_time_earning", "employee_as_written": "Priya", "item": "bonus", "amount_as_written": "₹12,000",
                      "unpaid_days_as_written": None, "effective_from": None, "month": None, "taxable": None, "explanation": ""}],
         "unclear": None},
        "Give Priya half a month's salary as bonus", run)
    assert not proposal["items"]
    assert "does not appear in the instruction" in proposal["problems"][0]


@pytest.mark.django_db
def test_ai_answers_are_saved_and_reused_not_asked_again(demo):
    from payroll.models import AIInteraction
    from payroll.features.payroll_runs import changes, runs
    from payroll.demo import setup

    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-07", maker)
    first = changes.propose(run, "Give Priya a ₹10,000 one-time bonus this month", maker)
    second = changes.propose(run, "Give Priya a ₹10,000 one-time bonus this month", maker)
    assert first.ai_id == second.ai_id
    assert second.proposal["ai"]["reused_saved_answer"] is True
    assert AIInteraction.objects.filter(purpose="instruction").count() == 1
    assert ChangeRequest.objects.filter(status="proposed").count() == 2


@pytest.mark.django_db
def test_ambiguous_employee_is_not_guessed(demo):
    from payroll.features.payroll_runs.changes import resolve_employee

    emps = [{"code": "1", "name": "Priya Sharma"}, {"code": "2", "name": "Priya Nair"}]
    emp, why = resolve_employee("Priya", emps)
    assert emp is None and "could be" in why


def test_ai_summary_with_invented_number_is_flagged():
    diff = {"totals": [{"code": "net_pay", "before": "100", "after": "200", "delta": "100"}], "employees": [], "rule_changes": []}
    allowed = tasks.numbers_in_diff(diff)
    assert "100" in allowed and "4242" not in tasks._amounts_in_text("Net pay rose by ₹4,242") & allowed


def test_claude_code_headless_provider(settings, monkeypatch):
    """Claude Code in headless mode: the CLI's JSON envelope is parsed; failures fall back safely."""
    import json as _json
    import subprocess

    from payroll.features.ai import client

    settings.AI_PROVIDER = "claude-code"
    monkeypatch.setattr(client, "_claude_code_bin", lambda: "/usr/bin/claude")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        envelope = {"type": "result", "subtype": "success", "is_error": False, "result": "",
                    "structured_output": {"headline": "ok", "points": []},
                    "modelUsage": {"claude-opus-5-5": {}}, "usage": {"input_tokens": 3, "output_tokens": 4}, "total_cost_usd": 0.01}
        return subprocess.CompletedProcess(cmd, 0, stdout=_json.dumps(envelope), stderr="")

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    data, usage = client._call_claude_code("system", "prompt", {"type": "object"})
    assert data == {"headline": "ok", "points": []} and usage["model_served"] == "claude-opus-5-5"
    cmd = calls[0]
    assert cmd[:2] == ["/usr/bin/claude", "-p"] and "--json-schema" in cmd and cmd[cmd.index("--tools") + 1] == ""

    monkeypatch.setattr(client.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Not logged in"))
    with pytest.raises(client.AIError, match="Not logged in"):
        client._call_claude_code("system", "prompt", {"type": "object"})


@pytest.mark.django_db
def test_ai_remark_does_not_block_a_valid_change(demo):
    from payroll.features.payroll_runs import changes, runs
    from payroll.demo import setup

    company, _ = demo
    run = runs.open_month(company, "2025-07", setup.system_user("maker"))
    out = changes.validate_proposal(
        {"changes": [{"type": "one_time_earning", "employee_as_written": "Priya", "item": "bonus", "amount_as_written": "₹10,000",
                      "unpaid_days_as_written": None, "effective_from": None, "month": None, "taxable": None, "explanation": ""}],
         "unclear": "Taxability not stated."}, "Give Priya a ₹10,000 one-time bonus this month", run)
    assert out["items"] and not out["problems"] and out["notes"] == ["Taxability not stated."]


@pytest.mark.django_db
def test_cancelling_a_month_discards_its_unconfirmed_changes(demo):
    from payroll.features.payroll_runs import changes, runs
    from payroll.demo import setup

    company, _ = demo
    maker = setup.system_user("maker")
    run = runs.open_month(company, "2025-07", maker)
    change = changes.propose(run, "Give Priya a ₹10,000 one-time bonus this month", maker)
    runs.cancel(run.pk, maker)
    change.refresh_from_db()
    assert change.status == "rejected"
