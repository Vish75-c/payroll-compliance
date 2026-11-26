"""Every number the engine produces is a Line that carries its own reason."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .money import to_str

# Line categories
EARNING = "earning"
DEDUCTION = "deduction"
EMPLOYER = "employer"
TOTAL = "total"
INFO = "info"


@dataclass
class RuleRef:
    """Which stored rule/setting version produced a number."""

    key: str
    version_id: str
    effective_from: str
    scope: str  # "statutory" | "company" | "default" | "override"
    jurisdiction: str = ""
    title: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "version_id": self.version_id,
            "effective_from": self.effective_from,
            "scope": self.scope,
            "jurisdiction": self.jurisdiction,
            "title": self.title,
        }


@dataclass
class Line:
    code: str
    label: str
    amount: Decimal
    category: str
    formula: str = ""  # arithmetic shown to the user: "12% × ₹15,000 = ₹1,800"
    reason: str = ""  # plain words: "PF is worked out on the first ₹15,000 of basic (company setting)"
    rules: list[RuleRef] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    children: list["Line"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "amount": to_str(self.amount),
            "category": self.category,
            "formula": self.formula,
            "reason": self.reason,
            "rules": [r.to_dict() for r in self.rules],
            "inputs": {k: _plain(v) for k, v in self.inputs.items()},
            "children": [c.to_dict() for c in self.children],
        }


def _plain(value):
    if isinstance(value, Decimal):
        return to_str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value
