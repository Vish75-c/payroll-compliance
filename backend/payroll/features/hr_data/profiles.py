"""Known HR tools: where their screens are and what the columns of their screens and exports mean.

A profile is written once by someone who has looked at the tool, checked against a sample export and a
test, and then used for every company on that tool: the field mapping is pre-filled without asking the
AI, and a person at each company confirms it once. Profiles live in ``integrations/<system>/profile.json``.
New ones come from layouts that companies have already mapped (``manage.py profile_candidates``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from .canonical import IGNORE, heuristic_mapping, is_valid_target, normalize_header, target_info

DATASETS = ("employees", "compensation", "leave", "attendance")
DATASET_GROUPS = {"employees": {"employee"}, "compensation": {"comp", "revision", "employee"}, "leave": {"leave", "attendance", "employee"},
                  "attendance": {"presence", "attendance", "employee"}}
KINDS = ("screen", "export")


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    system: str
    system_name: str
    verified_on: str
    id: str
    kind: str
    dataset: str
    match_on: tuple[str, ...]
    columns: dict
    needs: dict
    notes: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.system}/{self.id}@{self.verified_on}"


@dataclass(frozen=True)
class Profile:
    system: str
    name: str
    verified_on: str
    detect: dict
    screens: dict
    layouts: tuple[Layout, ...] = field(default=())


@dataclass
class LayoutMatch:
    layout: Layout
    mapping: dict[str, str]           # every column of the table -> target (or "ignore")
    suggestion: dict[str, dict]       # every column -> {"target", "confidence", "reason"}
    missing_needs: dict[str, str]     # column the layout needs but the table lacks -> how to fix it


def _load(path: Path) -> Profile:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{path}: not readable JSON ({exc})") from exc
    where = str(path)
    for key in ("system", "name", "verified_on"):
        if not raw.get(key):
            raise ProfileError(f"{where}: '{key}' is missing")
    layouts = []
    for i, lay in enumerate(raw.get("layouts", [])):
        at = f"{where}, layout {lay.get('id') or i + 1}"
        if lay.get("kind") not in KINDS or lay.get("dataset") not in DATASETS or not lay.get("id"):
            raise ProfileError(f"{at}: needs an id, kind ({'/'.join(KINDS)}) and dataset ({'/'.join(DATASETS)})")
        columns = lay.get("columns") or {}
        bad = [f"{c} -> {t}" for c, t in columns.items() if not is_valid_target(t)]
        if bad:
            raise ProfileError(f"{at}: unknown payroll field(s): {', '.join(bad)}")
        match_on = lay.get("match_on") or []
        if not match_on or any(c not in columns for c in match_on):
            raise ProfileError(f"{at}: every 'match_on' column must also be listed in 'columns'")
        needs = lay.get("needs") or {}
        if any(c not in columns for c in needs):
            raise ProfileError(f"{at}: every column in 'needs' must also be listed in 'columns'")
        layouts.append(Layout(
            system=raw["system"], system_name=raw["name"], verified_on=raw["verified_on"], id=lay["id"], kind=lay["kind"],
            dataset=lay["dataset"], match_on=tuple(match_on), columns=dict(columns), needs=dict(needs), notes=tuple(lay.get("notes", [])),
        ))
    screens = raw.get("screens") or {}
    for dataset in (screens.get("datasets") or {}):
        if dataset not in DATASETS:
            raise ProfileError(f"{where}: unknown screen '{dataset}'")
    return Profile(system=raw["system"], name=raw["name"], verified_on=raw["verified_on"], detect=raw.get("detect") or {},
                   screens=screens, layouts=tuple(layouts))


def load_profiles(directory: str | None = None) -> dict[str, Profile]:
    """The known HR tools, or none when HR_TOOL_PROFILES is off (every tool is then read as unknown)."""
    if not getattr(settings, "HR_TOOL_PROFILES", True):
        return {}
    return _load_profiles(directory)


@lru_cache(maxsize=None)
def _load_profiles(directory: str | None = None) -> dict[str, Profile]:
    root = Path(directory) if directory else Path(settings.REPO_DIR) / "integrations"
    out: dict[str, Profile] = {}
    for path in sorted(root.glob("*/profile.json")):
        profile = _load(path)
        if profile.system in out:
            raise ProfileError(f"{path}: system '{profile.system}' is defined twice")
        out[profile.system] = profile
    return out


load_profiles.cache_clear = _load_profiles.cache_clear


def catalogue() -> list[dict]:
    return [{"system": p.system, "name": p.name, "verified_on": p.verified_on, "has_screens": bool(p.screens.get("datasets")),
             "layouts": [{"id": lay.id, "kind": lay.kind, "dataset": lay.dataset} for lay in p.layouts]}
            for p in load_profiles().values()]


def profile_for(system: str | None) -> Profile | None:
    """By key ("horilla") or by the name a connector shows ("Horilla")."""
    if not system:
        return None
    wanted = system.strip().lower()
    return next((p for p in load_profiles().values() if wanted in (p.system, p.name.lower())), None)


def detect_system(page_title: str, page_text: str = "") -> Profile | None:
    """Recognise a known HR tool from the page seen after signing in."""
    title, text = (page_title or "").lower(), (page_text or "").lower()
    for p in load_profiles().values():
        if any(w.lower() in title for w in p.detect.get("title_contains", [])):
            return p
        if any(w.lower() in text for w in p.detect.get("page_text", [])):
            return p
    return None


def screens_for(profile: Profile) -> dict[str, dict]:
    """The profile's screens in the shape of a connector recipe's datasets."""
    out = {}
    for dataset, spec in (profile.screens.get("datasets") or {}).items():
        out[dataset] = {"url": spec["url"], "follow_row_links": bool(spec.get("follow_row_links")),
                        "reason": f"{profile.name} profile" + (f": {spec['note']}" if spec.get("note") else "")}
    return out


def match_layout(headers: list[str], dataset_hint: str = "", system: str = "") -> LayoutMatch | None:
    """The known layout these columns come from, if any. All of a layout's ``match_on`` columns must be
    present; extra columns are fine (exports let people pick columns). Ties prefer the connector's own
    tool, then the kind of data the file was uploaded as, then the layout that explains most columns."""
    present = {normalize_header(h): h for h in headers}
    own = profile_for(system)
    best, best_score = None, None
    for profile in load_profiles().values():
        for lay in profile.layouts:
            if any(normalize_header(c) not in present for c in lay.match_on):
                continue
            covered = sum(1 for c in lay.columns if normalize_header(c) in present)
            score = (bool(own and own.system == profile.system), lay.dataset == dataset_hint, covered)
            if best_score is None or score > best_score:
                best, best_score = lay, score
    if best is None:
        return None

    by_norm = {normalize_header(c): t for c, t in best.columns.items()}
    mapping, suggestion = {}, {}
    for h in headers:
        target = by_norm.get(normalize_header(h))
        if target is not None:
            mapping[h] = target
            suggestion[h] = {"target": target, "confidence": 1.0, "reason": f"{best.system_name} profile ({best.id})"}
    # A column the profile does not list (e.g. one picked in the export dialog, or a field from a detail page) is
    # matched only when its name is an exact known name for a still-free field OF THIS KIND OF DATA: a "Basic
    # Salary" shown on an employee's page never becomes a salary. Anything else is left for a person.
    rest = [h for h in headers if h not in mapping]
    guess = heuristic_mapping(rest, best.dataset) if rest else {}
    taken = {t for t in mapping.values() if t != IGNORE}
    kinds = DATASET_GROUPS[best.dataset]
    for h in rest:
        g = guess.get(h) or {}
        if (g.get("confidence", 0) >= 1.0 and g["target"] != IGNORE and g["target"] not in taken
                and target_info(g["target"])["group"] in kinds):
            mapping[h] = g["target"]
            taken.add(g["target"])
            suggestion[h] = {**g, "reason": f"not in the {best.system_name} profile; {g['reason']}"}
        else:
            mapping[h] = IGNORE
            suggestion[h] = {"target": IGNORE, "confidence": 0.5, "reason": f"not in the {best.system_name} profile; check it"}
    missing = {c: msg for c, msg in best.needs.items() if normalize_header(c) not in present}
    return LayoutMatch(layout=best, mapping=mapping, suggestion=suggestion, missing_needs=missing)
