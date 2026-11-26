"""Names and order of the parts of pay, which pay a choice like "gross this month" means, and the engine's error."""
from __future__ import annotations

from decimal import Decimal

COMPONENT_ORDER = ["basic", "da", "hra", "special_allowance", "conveyance", "medical", "lta", "other_allowance"]
COMPONENT_LABELS = {
    "basic": "Basic",
    "da": "Dearness allowance",
    "hra": "HRA",
    "special_allowance": "Special allowance",
    "conveyance": "Conveyance allowance",
    "medical": "Medical allowance",
    "lta": "Leave travel allowance",
    "other_allowance": "Other allowance",
}

WAGE_LABELS = {
    "gross_this_month": "gross pay this month",
    "gross_excluding_one_time": "gross pay without one-time items",
    "regular_earned": "regular pay earned this month (without arrears and one-time items)",
    "contracted_monthly": "full monthly salary (before unpaid leave)",
}


def component_label(code: str) -> str:
    return COMPONENT_LABELS.get(code) or code.replace("_", " ").capitalize()


def order_components(codes) -> list[str]:
    codes = set(codes)
    known = [c for c in COMPONENT_ORDER if c in codes]
    return known + sorted(codes - set(known))


class CalculationError(Exception):
    pass


def wage_for(choice: str, regular: Decimal, arrears: Decimal, one_time: Decimal, contracted: Decimal) -> Decimal:
    if choice == "gross_this_month":
        return regular + arrears + one_time
    if choice == "gross_excluding_one_time":
        return regular + arrears
    if choice == "regular_earned":
        return regular
    if choice == "contracted_monthly":
        return contracted
    raise CalculationError(f"unknown wage choice {choice!r}")
