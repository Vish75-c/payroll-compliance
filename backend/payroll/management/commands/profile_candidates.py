"""Draft HR tool profiles from layouts that companies have already mapped (for an engineer to review).

Every company confirms its own mappings, so knowledge about a new HR tool builds up one company at a
time. This command collects confirmed mappings whose columns match no known profile, groups the ones
with the same columns, and writes draft layouts with how many companies agree. Only column names,
payroll fields and leave type/status decisions are written; never a row of anyone's data. A person turns
a draft into ``integrations/<system>/profile.json`` with a sample export and a test.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from payroll.features.hr_data import profiles
from payroll.features.hr_data.canonical import IGNORE, normalize_header, target_info
from payroll.models import FieldMapping

_GROUP_TO_DATASET = {"comp": "compensation", "revision": "compensation", "leave": "leave", "attendance": "leave"}


def _dataset(mapping: dict) -> str:
    groups = {target_info(t)["group"] for t in mapping.values() if t != IGNORE}
    for group in ("comp", "revision", "leave", "attendance"):
        if group in groups:
            return _GROUP_TO_DATASET[group]
    return "employees"


def collect() -> dict[str, dict]:
    """{system slug: draft profile}. A company's newest confirmed mapping of a layout is its answer."""
    newest: dict[tuple, FieldMapping] = {}
    for fm in FieldMapping.objects.filter(confirmed_at__isnull=False).order_by("confirmed_at", "id"):
        layout = frozenset(normalize_header(h) for h in fm.headers)
        newest[(fm.company_id, fm.source_system, layout)] = fm
    groups: dict[tuple, list[FieldMapping]] = defaultdict(list)
    for (_, system, layout), fm in newest.items():
        if profiles.match_layout(fm.headers, system=system):
            continue  # already in a profile
        groups[(system, layout)].append(fm)

    drafts: dict[str, dict] = {}
    for (system, _), fms in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        answers = Counter(tuple(sorted((normalize_header(h), t) for h, t in fm.mapping.items())) for fm in fms)
        (agreed, agreeing), *others = answers.most_common()
        example = max((fm for fm in fms if tuple(sorted((normalize_header(h), t) for h, t in fm.mapping.items())) == agreed),
                      key=lambda fm: fm.confirmed_at)
        slug = slugify(system) or "unknown"
        draft = drafts.setdefault(slug, {
            "system": slug, "name": system, "verified_on": "", "draft": True,
            "generated_on": timezone.localdate().isoformat(), "layouts": [],
        })
        dataset = _dataset(example.mapping)
        kind = "export" if system.lower() == "spreadsheet" else "screen"
        layout_id = f"{dataset}-{kind}"
        n = sum(1 for lay in draft["layouts"] if lay["id"].startswith(layout_id))
        draft["layouts"].append({
            "id": f"{layout_id}-{n + 1}" if n else layout_id, "kind": kind, "dataset": dataset,
            "match_on": [h for h in example.headers if example.mapping.get(h, IGNORE) != IGNORE],
            "columns": {h: example.mapping.get(h, IGNORE) for h in example.headers},
            "value_maps": example.value_maps,
            "seen_at_companies": len(fms), "agreeing_companies": agreeing,
            "other_answers": [{"companies": count, "differs_on": sorted({h for h, _ in set(answer) ^ set(agreed)})}
                              for answer, count in others],
            "table_name": example.table_name,
        })
    return drafts


class Command(BaseCommand):
    help = "Write draft HR tool profiles from confirmed mappings that match no known profile (no row data)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.DATA_DIR) / "profile_candidates"))

    def handle(self, *args, **opts):
        drafts = collect()
        if not drafts:
            self.stdout.write("Every confirmed mapping matches a known profile; nothing to draft.")
            return
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        for slug, draft in drafts.items():
            path = out / f"{slug}.json"
            path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
            summary = ", ".join(f"{lay['id']} ({lay['agreeing_companies']}/{lay['seen_at_companies']} companies agree)"
                                for lay in draft["layouts"])
            self.stdout.write(f"{path}: {summary}")
        self.stdout.write("Review each draft, add a sample export under integrations/<system>/fixtures/ and a test, "
                          "then save it as integrations/<system>/profile.json.")
