"""Professional tax: picking the state's slab (by pay, gender and month) and describing it."""
from __future__ import annotations

from decimal import Decimal

from .money import d, inr


def select_pt_slab(slabs: list[dict], basis: Decimal, month: int, gender: str | None):
    best = None
    best_key = None
    for slab in slabs:
        if slab.get("gender") and slab["gender"] != (gender or ""):
            continue
        if slab.get("months") and month not in slab["months"]:
            continue
        if "min" in slab:
            lower = d(slab["min"])
            if basis < lower:
                continue
        else:
            lower = d(slab["above"])
            if basis <= lower:
                continue
        if slab.get("max") is not None and basis > d(slab["max"]):
            continue
        specificity = (2 if slab.get("months") else 0) + (1 if slab.get("gender") else 0)
        key = (specificity, lower)
        if best_key is None or key > best_key:
            best, best_key = slab, key
    return best


def _slab_text(slab: dict) -> str:
    if "min" in slab:
        text = f"{inr(d(slab['min']))} or more"
    else:
        text = f"more than {inr(d(slab['above']))}"
    if slab.get("max") is not None:
        text += f" up to {inr(d(slab['max']))}"
    if slab.get("gender"):
        text += {"F": " (women)", "M": " (men)", "O": " (others)"}[slab["gender"]]
    if slab.get("months"):
        text += " in month " + ", ".join(str(m) for m in slab["months"])
    return text
