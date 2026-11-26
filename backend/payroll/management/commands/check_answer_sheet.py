"""Run answer-sheet cases end to end and report which numbers match (no AI involved).

    check_answer_sheet                                   every folder in answer_sheets/
    check_answer_sheet answer_sheets/case_A              one folder (choices.json + expected.csv)
    check_answer_sheet --expected case_C.csv --base A --set lop.divisor=26 --set pf_policy.wage_base=full
                                                         a case given as a CSV only: case A's choices with these changes
"""
import json
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from payroll.demo.answer_sheet import build_choices, format_report, run_case


class Command(BaseCommand):
    help = "Check the calculation against answer sheets, e.g. `check_answer_sheet answer_sheets/case_A answer_sheets/case_B`."

    def add_arguments(self, parser):
        parser.add_argument("cases", nargs="*", help="Case folders (default: every folder in answer_sheets/)")
        parser.add_argument("--expected", help="Use this CSV instead of the folder's expected.csv")
        parser.add_argument("--data", help="Use this HR spreadsheet instead of the one named in choices.json")
        parser.add_argument("--json", help="Also write the full report to this JSON file")
        parser.add_argument("--base", help="With --expected and no folder: start from this case's choices (A, B or a folder)")
        parser.add_argument("--set", action="append", default=[], metavar="SETTING.FIELD=VALUE",
                            help="Change one company choice of --base, e.g. lop.divisor=26 (repeatable)")
        parser.add_argument("--name", default="C", help="Name of a case given with --base (default C)")
        parser.add_argument("--save", action="store_true",
                            help="With --base: also save the case as answer_sheets/case_<name>/ so `make test` runs it")

    def _folder(self, name: str) -> Path:
        root = Path(settings.REPO_DIR) / "answer_sheets"
        for candidate in (Path(name), Path.cwd() / name, Path(settings.REPO_DIR) / name, root / name, root / f"case_{name}"):
            if (candidate / "choices.json").exists():
                return candidate.resolve()
        raise CommandError(f"No case folder with a choices.json found for {name!r}")

    def handle(self, *args, **opts):
        root = Path(settings.REPO_DIR) / "answer_sheets"
        jobs = []  # (folder, choices or None)
        if opts["base"]:
            if opts["cases"] or not opts["expected"]:
                raise CommandError("--base needs --expected <csv> and no case folders")
            base = self._folder(opts["base"])
            try:
                choices = build_choices(json.loads((base / "choices.json").read_text()), opts["set"], opts["name"])
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(f"Case {opts['name']}: choices of {base.name} with {', '.join(opts['set']) or 'no changes'}")
            self.stdout.write(json.dumps(choices["settings"], indent=2))
            if opts["save"]:
                target = root / f"case_{opts['name']}"
                target.mkdir(parents=True, exist_ok=True)
                (target / "choices.json").write_text(json.dumps(choices, indent=2, ensure_ascii=False) + "\n")
                (target / "expected.csv").write_bytes(Path(opts["expected"]).read_bytes())
                self.stdout.write(f"Saved as {target} (choices.json + expected.csv)")
            jobs.append((base, choices))
        elif opts["set"]:
            raise CommandError("--set needs --base")
        else:
            folders = [self._folder(c) for c in opts["cases"]] or sorted(
                p for p in root.iterdir() if (p / "choices.json").exists())
            jobs = [(f, None) for f in folders]

        reports, failed = [], False
        for folder, choices in jobs:
            self.stdout.write(f"Running {(choices or {}).get('case') or folder.name} ...")
            report = run_case(folder, opts.get("expected"), opts.get("data"), log=self.stdout.write, choices=choices)
            reports.append(report)
            self.stdout.write(format_report(report))
            ok = report["matched"] == report["total"]
            failed = failed or not ok
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(f"=> {report['matched']}/{report['total']} match\n"))
        if opts.get("json"):
            Path(opts["json"]).write_text(json.dumps(reports, indent=2))
        if failed:
            sys.exit(1)
