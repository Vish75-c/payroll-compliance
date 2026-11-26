"""What changed between two calculated months (or two calculations of the same month), and why.

The "why" is worked out from the data, not guessed: a changed salary, arrears, a one-time
item, a different number of unpaid days, or a different rule/setting version.
"""
from __future__ import annotations

import re

from .money import d, inr, to_str

COMPARE_PREFIXES = ("earned.", "one_time.", "deduction.")
COMPARE_KEYS = [
    "fixed_gross", "unpaid_days", "lop_deduction", "arrears", "gross", "pf_employee", "esi_employee", "pt", "tds",
    "total_deductions", "net_pay", "pf_employer", "esi_employer", "employer_cost",
]
LABELS = {
    "fixed_gross": "Monthly salary",
    "unpaid_days": "Unpaid days",
    "lop_deduction": "Unpaid leave cut",
    "arrears": "Arrears",
    "gross": "Gross pay",
    "pf_employee": "PF (employee)",
    "esi_employee": "ESI (employee)",
    "pt": "Professional tax",
    "tds": "Income tax (TDS)",
    "total_deductions": "Total deductions",
    "net_pay": "Net pay",
    "pf_employer": "PF (company)",
    "esi_employer": "ESI (company)",
    "employer_cost": "Cost to company",
}


def _line_index(lines, out=None):
    out = {} if out is None else out
    for line in lines:
        out[line["code"]] = line
        _line_index(line.get("children", []), out)
    return out


def _label(code, index):
    if code in index:
        return index[code]["label"]
    return LABELS.get(code, code)


def _rules(result) -> dict[tuple[str, str], dict]:
    return {(r["key"], r["jurisdiction"]): r for r in result.get("rules_used", [])}


def compare_results(before: dict, after: dict, before_label: str = "before", after_label: str = "after") -> dict:
    rules_before, rules_after = _rules(before), _rules(after)
    rule_changes = []
    for key in sorted(set(rules_before) | set(rules_after)):
        b, a = rules_before.get(key), rules_after.get(key)
        # A rule that only one side needed (e.g. arrears settings in a month with arrears) did not change.
        if not (b and a) or (b["version_id"], b["effective_from"]) == (a["version_id"], a["effective_from"]):
            continue
        rule_changes.append({
            "key": key[0],
            "jurisdiction": key[1],
            "title": (a or b)["title"],
            "before": b and {"version_id": b["version_id"], "effective_from": b["effective_from"], "scope": b["scope"]},
            "after": a and {"version_id": a["version_id"], "effective_from": a["effective_from"], "scope": a["scope"]},
        })
    changed_rule_keys = {(c["key"], c["jurisdiction"]) for c in rule_changes}

    emps_before = {e["code"]: e for e in before.get("employees", [])}
    emps_after = {e["code"]: e for e in after.get("employees", [])}
    employees = []
    for code in sorted(set(emps_before) | set(emps_after)):
        b, a = emps_before.get(code), emps_after.get(code)
        if b is None or a is None:
            present = a or b
            employees.append({
                "code": code,
                "name": present["name"],
                "status": "added" if a else "removed",
                "net_before": b and b["amounts"]["net_pay"],
                "net_after": a and a["amounts"]["net_pay"],
                "net_delta": to_str(d(a and a["amounts"]["net_pay"]) - d(b and b["amounts"]["net_pay"])),
                "changes": [],
                "drivers": [{"kind": "joined" if a else "left",
                             "text": f"{present['name']} is {'new in' if a else 'not in'} {after_label}"}],
            })
            continue
        index_a = _line_index(a["lines"])
        index_b = _line_index(b["lines"])
        keys = [k for k in COMPARE_KEYS]
        extra = sorted({k for k in list(a["amounts"]) + list(b["amounts"]) if k.startswith(COMPARE_PREFIXES)})
        changes = []
        for key in extra + keys:
            vb, va = d(b["amounts"].get(key)), d(a["amounts"].get(key))
            if vb == va:
                continue
            la, lb = index_a.get(key), index_b.get(key)
            changes.append({
                "code": key,
                "label": _label(key, index_a) if la else _label(key, index_b),
                "before": to_str(vb),
                "after": to_str(va),
                "delta": to_str(va - vb),
                "formula_before": lb["formula"] if lb else "",
                "formula_after": la["formula"] if la else "",
                "reason_after": la["reason"] if la else "",
            })
        drivers = _drivers(b, a, before_label, after_label, changed_rule_keys, rule_changes, index_a)
        employees.append({
            "code": code,
            "name": a["name"],
            "status": "changed" if changes else "unchanged",
            "net_before": b["amounts"]["net_pay"],
            "net_after": a["amounts"]["net_pay"],
            "net_delta": to_str(d(a["amounts"]["net_pay"]) - d(b["amounts"]["net_pay"])),
            "changes": changes,
            "drivers": drivers if changes else [],
        })

    totals = []
    for key in sorted(set(before.get("totals", {})) | set(after.get("totals", {}))):
        vb, va = d(before["totals"].get(key)), d(after["totals"].get(key))
        totals.append({"code": key, "label": LABELS.get(key, key.split(".", 1)[-1].replace("_", " ").capitalize()),
                       "before": to_str(vb), "after": to_str(va), "delta": to_str(va - vb)})
    return {
        "before_label": before_label,
        "after_label": after_label,
        "rule_changes": rule_changes,
        "employees": employees,
        "totals": totals,
        "changed_count": sum(1 for e in employees if e["status"] != "unchanged"),
    }


def _drivers(b, a, before_label, after_label, changed_rule_keys, rule_changes, index_a):
    drivers = []
    am, bm = a["amounts"], b["amounts"]
    if d(am.get("fixed_gross")) != d(bm.get("fixed_gross")):
        drivers.append({"kind": "salary", "text": f"Monthly salary changed from {inr(d(bm.get('fixed_gross')))} to {inr(d(am.get('fixed_gross')))}"})
    if d(am.get("arrears")) != d(bm.get("arrears")):
        line = index_a.get("arrears")
        if d(am.get("arrears")):
            months = ", ".join(c["label"].replace("Arrears for ", "") for c in (line or {}).get("children", []))
            drivers.append({"kind": "arrears", "text": f"Arrears of {inr(d(am['arrears']))} for {months}: the salary for those months was revised after they were paid"})
        else:
            drivers.append({"kind": "arrears", "text": f"No arrears in {after_label} (there were {inr(d(bm.get('arrears')))} in {before_label})"})
    one_time_keys = sorted({k for k in list(am) + list(bm) if k.startswith(("one_time.", "deduction."))})
    for key in one_time_keys:
        va, vb = d(am.get(key)), d(bm.get(key))
        if va != vb:
            label = (index_a.get(key) or {}).get("label") or key.split(".", 1)[1].replace("_", " ")
            if va and not vb:
                drivers.append({"kind": "one_time", "text": f"One-time {label.lower()} of {inr(va)} added"})
            elif vb and not va:
                drivers.append({"kind": "one_time", "text": f"One-time {label.lower()} of {inr(vb)} in {before_label} is not repeated"})
            else:
                drivers.append({"kind": "one_time", "text": f"{label} changed from {inr(vb)} to {inr(va)}"})
    ua, ub = d(am.get("unpaid_days")), d(bm.get("unpaid_days"))
    if ua != ub:
        drivers.append({"kind": "unpaid_days", "text": f"Unpaid days: {to_str(ub)} in {before_label}, {to_str(ua)} in {after_label}"})
    elif ua and am.get("days_in_month") != bm.get("days_in_month"):
        drivers.append({"kind": "days_in_month", "text": f"Same unpaid days, but the month has {am.get('days_in_month')} days instead of {bm.get('days_in_month')}"})
    lines_a = []
    _flatten(a["lines"], lines_a)
    used_keys = {(r["key"], r["jurisdiction"]) for line in lines_a for r in line["rules"]}
    for change in rule_changes:
        k = (change["key"], change["jurisdiction"])
        if k in used_keys and k in changed_rule_keys:
            after = change["after"]
            where = f" ({change['jurisdiction']})" if change["jurisdiction"] else ""
            if after and after["scope"] == "override":
                text = f"What-if value used for '{change['title']}'{where}"
            elif after:
                text = f"'{change['title']}'{where} now uses the version that starts {after['effective_from']}"
            else:
                text = f"'{change['title']}'{where} no longer applies"
            drivers.append({"kind": "rule", "text": text})
    if not drivers:
        drivers.append({"kind": "other", "text": "Amounts moved with the items above (for example deductions follow the new gross pay)"})
    return drivers


def _flatten(lines, out):
    for line in lines:
        out.append(line)
        _flatten(line.get("children", []), out)


def numbers_in_diff(diff: dict) -> set[str]:
    """All amounts that appear in a diff, as plain integers/decimals. Used to check AI-written summaries."""
    values: set[str] = set()

    def add(v):
        if v in (None, ""):
            return
        dv = d(v)
        for x in (dv, abs(dv)):
            values.add(to_str(x))
    for t in diff.get("totals", []):
        for k in ("before", "after", "delta"):
            add(t[k])
    for e in diff.get("employees", []):
        for k in ("net_before", "net_after", "net_delta"):
            add(e.get(k))
        for c in e.get("changes", []):
            for k in ("before", "after", "delta"):
                add(c[k])
        for drv in e.get("drivers", []):
            for m in re.findall(r"₹([\d,]+(?:\.\d+)?)", drv["text"]):
                add(m.replace(",", ""))
            for m in re.findall(r"\b(\d+(?:\.\d+)?)\b", drv["text"]):
                add(m)
    return values

