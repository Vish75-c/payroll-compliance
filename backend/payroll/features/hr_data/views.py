from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll.shared.permissions import RuleViolation, is_in_group

from ..ai import present as ai_present
from ..companies.models import Company
from . import jobs as ingestion_jobs
from . import present as P
from . import profiles
from . import snapshots as snapshot_service
from .canonical import GROUP_LABELS, catalogue
from .models import Connector, DataSnapshot, IngestionJob, RawTable
from .normalize import default_value_maps

# What a screen's confirmed path carries besides its address: the clicks after it, the tabs on each record's page,
# and (to show the person) how it was found, with a screenshot per step.
PATH_KEYS = ("steps", "detail_steps", "detail_fields", "learned_path", "found_by")


def _clean_steps(steps) -> list[dict]:
    """Only clicks, each named by its role and visible words: nothing a person could not see on the screen."""
    out = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict) and s.get("action", "click") == "click" and isinstance(s.get("role"), str) and isinstance(s.get("text"), str):
            out.append({"action": "click", "role": s["role"][:30], "text": s["text"][:80]})
    return out


def _clean_path(spec: dict) -> dict:
    out = {}
    if steps := _clean_steps(spec.get("steps")):
        out["steps"] = steps
    if (tabs := _clean_steps(spec.get("detail_steps"))) and spec.get("follow_row_links"):
        out["detail_steps"] = tabs
    if isinstance(spec.get("learned_path"), list):
        out["learned_path"] = [{k: str(s.get(k, ""))[:300] for k in ("role", "text", "reason", "screenshot")}
                               for s in spec["learned_path"] if isinstance(s, dict)]
    for k in ("detail_fields", "found_by"):
        if spec.get(k):
            out[k] = spec[k]
    return out


@api_view(["GET"])
def hr_tool_profiles(request):
    """HR tools the system already knows: their screens and export layouts are mapped without AI."""
    return Response(profiles.catalogue())


@api_view(["GET"])
def employees(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    return Response([P.employee(e) for e in company.employees.order_by("code")])


@api_view(["GET", "POST"])
def connectors(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if request.method == "POST":
        if not is_in_group(request.user, "maker"):
            raise RuleViolation("Only a finance team employee can add an HR tool.", code="forbidden")
        data = request.data
        if not data.get("name") or not data.get("base_url"):
            raise RuleViolation("Give the HR tool a name and its web address.")
        known = profiles.profile_for(data.get("profile"))
        if data.get("profile") and not known:
            raise RuleViolation(f"Unknown HR tool profile: {data['profile']}")
        conn = Connector.objects.create(company=company, name=data["name"], kind=Connector.KIND_BROWSER,
                                        system_name=data.get("system_name") or (known.name if known else data["name"]),
                                        base_url=data["base_url"], username=data.get("username", ""), password=data.get("password", ""),
                                        profile=known.system if known else "",
                                        # A known tool's screens are suggested straight away; a person still confirms them.
                                        recipe={"datasets": profiles.screens_for(known)} if known else {})
        return Response(P.connector(conn), status=201)
    return Response([P.connector(c) for c in company.connectors.order_by("id")])


@api_view(["PATCH", "DELETE"])
def connector_detail(request, connector_id):
    conn = get_object_or_404(Connector, pk=connector_id)
    if not is_in_group(request.user, "maker"):
        raise RuleViolation("Only a finance team employee can change an HR tool connection.", code="forbidden")
    if request.method == "DELETE":
        conn.delete()
        return Response(status=204)
    for field in ("name", "system_name", "base_url", "username"):
        if field in request.data:
            setattr(conn, field, request.data[field])
    if request.data.get("password"):
        conn.password = request.data["password"]
    conn.save()
    return Response(P.connector(conn))


@api_view(["POST"])
def connector_discover(request, connector_id):
    """The agent logs in and suggests which screens hold employees, salary and leave. A person confirms."""
    from .browser_agent import AgentError, BrowserAgent

    conn = get_object_or_404(Connector, pk=connector_id)
    if not is_in_group(request.user, "maker"):
        raise RuleViolation("Only a finance team employee can connect an HR tool.", code="forbidden")
    messages = []
    agent = BrowserAgent(conn, lambda msg, level="info", **kw: messages.append({"level": level, "message": msg}))
    try:
        found = agent.discover(conn.company, force=bool(request.data.get("force")))
    except AgentError as exc:
        raise RuleViolation(str(exc)) from exc
    recipe = {"login_url": conn.recipe.get("login_url", ""), "datasets": {}}
    for key, pick in found["datasets"].items():
        previous = (conn.recipe.get("datasets") or {}).get(key) or {}
        recipe["datasets"][key] = {"url": pick["url"] if pick else "", "reason": pick["reason"] if pick else "no matching link",
                                   "follow_row_links": (pick or {}).get("follow_row_links", previous.get("follow_row_links", False)),
                                   **{k: pick[k] for k in PATH_KEYS if pick and pick.get(k)}}
    conn.recipe = recipe
    conn.recipe_ai = found["interaction"]
    if found["profile"]:
        conn.profile = found["profile"].system
    conn.recipe_confirmed_at = None
    conn.recipe_confirmed_by = None
    conn.save()
    return Response({"connector": P.connector(conn), "links": found["links"], "log": messages, "ai": ai_present.ai(found["interaction"])})


@api_view(["POST"])
def connector_confirm_recipe(request, connector_id):
    conn = get_object_or_404(Connector, pk=connector_id)
    if not is_in_group(request.user, "maker"):
        raise RuleViolation("Only a finance team employee can confirm screens.", code="forbidden")
    datasets = request.data.get("datasets") or {}
    clean = {}
    for key in ("employees", "compensation", "leave"):
        spec = datasets.get(key) or {}
        if spec.get("url"):
            clean[key] = {"url": spec["url"], "reason": spec.get("reason", ""), "follow_row_links": bool(spec.get("follow_row_links")),
                          **_clean_path(spec)}
    if not clean.get("employees"):
        raise RuleViolation("At least the employees screen is needed.")
    from .browser_agent import DATASETS, off_site

    login_url = request.data.get("login_url", conn.recipe.get("login_url", ""))
    wrong = [f"{label} screen {spec['url']} is on {other}" for key, label in DATASETS.items()
             if (spec := clean.get(key)) and (other := off_site(spec["url"], conn.base_url))]
    if login_url and (other := off_site(login_url, conn.base_url)):
        wrong.append(f"login page {login_url} is on {other}")
    if wrong:
        raise RuleViolation(f"Every screen must be on {conn.name} ({conn.base_url}): " + "; ".join(wrong)
                            + ". Use a path such as /employees, or add the other HR tool as its own connection.", code="off_site")
    conn.recipe = {"login_url": login_url, "datasets": clean}
    conn.recipe_confirmed_by = request.user
    conn.recipe_confirmed_at = timezone.now()
    conn.save()
    if conn.recipe_ai and conn.recipe_ai.status == "pending_review":
        conn.recipe_ai.status = "accepted"
        conn.recipe_ai.reviewed_by = request.user
        conn.recipe_ai.reviewed_at = timezone.now()
        conn.recipe_ai.save()
    return Response(P.connector(conn))


@api_view(["POST"])
def connector_read(request, connector_id):
    conn = get_object_or_404(Connector, pk=connector_id)
    job = ingestion_jobs.start_browser_job(conn, request.user)
    return Response(P.job(job), status=202)


@api_view(["POST"])
def upload(request, company_id):
    """One workbook with everything (field "file"), or a file per kind of data ("employees", "compensation", "leave")."""
    company = get_object_or_404(Company, pk=company_id)
    if not is_in_group(request.user, "maker"):
        raise RuleViolation("Only a finance team employee can upload HR data.", code="forbidden")
    job = ingestion_jobs.run_spreadsheet_job(company, request.user, _uploaded_files(request))
    return Response(P.job(job, detail=True), status=201)


@api_view(["POST"])
def job_add_files(request, job_id):
    job = get_object_or_404(IngestionJob, pk=job_id)
    ingestion_jobs.add_spreadsheet_files(job, request.user, _uploaded_files(request))
    job.refresh_from_db()
    return Response(P.job(job, detail=True))


def _uploaded_files(request) -> list[tuple[str, bytes, str]]:
    # Form field -> kind of data in the file ("" = anything, e.g. one workbook with every sheet).
    fields = {"file": "", **{k: k for k in ingestion_jobs.UPLOAD_DATASETS if k}}
    unknown = set(request.FILES) - set(fields)
    if unknown:
        raise RuleViolation(f"Unknown upload field(s): {', '.join(sorted(unknown))}")
    files = []
    for field, dataset in fields.items():
        for f in request.FILES.getlist(field):
            if f.size > 10 * 1024 * 1024:
                raise RuleViolation(f"{f.name} is larger than 10 MB.")
            files.append((f.name, f.read(), dataset))
    if not files:
        raise RuleViolation("Choose a file to upload.")
    return files


@api_view(["GET"])
def jobs(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    return Response([P.job(ingestion_jobs.mark_if_interrupted(j)) for j in company.ingestion_jobs.order_by("-id")[:30]])


@api_view(["GET"])
def job_detail(request, job_id):
    job = ingestion_jobs.mark_if_interrupted(get_object_or_404(IngestionJob, pk=job_id))
    return Response(P.job(job, detail=True))


@api_view(["POST"])
def job_suggest(request, job_id):
    job = get_object_or_404(IngestionJob, pk=job_id)
    ingestion_jobs.suggest(job, request.user, force=bool(request.data.get("force")))
    job.refresh_from_db()
    return Response(P.job(job, detail=True))


@api_view(["POST"])
def table_confirm(request, table_id):
    table = get_object_or_404(RawTable, pk=table_id)
    ingestion_jobs.confirm(table, request.user, request.data.get("mapping") or {}, request.data.get("value_maps"),
                           request.data.get("options"))
    table.job.refresh_from_db()
    return Response(P.job(table.job, detail=True))


@api_view(["POST"])
def table_value_maps(request, table_id):
    """Preview which leave types/statuses a mapping would treat as unpaid/counted, before confirming."""
    table = get_object_or_404(RawTable, pk=table_id)
    return Response(default_value_maps(table.rows, request.data.get("mapping") or {}))


@api_view(["POST"])
def job_build_snapshot(request, job_id):
    job = get_object_or_404(IngestionJob, pk=job_id)
    if not is_in_group(request.user, "maker"):
        raise RuleViolation("Only a finance team employee can do this.", code="forbidden")
    snap = snapshot_service.create_from_job(job, request.user)
    return Response(P.snapshot(snap), status=201)


@api_view(["GET"])
def mapping_catalogue(request):
    return Response({"targets": catalogue(), "groups": GROUP_LABELS})


@api_view(["GET"])
def snapshots(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    return Response([P.snapshot_summary(s) for s in company.snapshots.order_by("-id")[:30]])


@api_view(["GET"])
def snapshot_detail(request, snapshot_id):
    return Response(P.snapshot(get_object_or_404(DataSnapshot, pk=snapshot_id)))


@api_view(["POST"])
def snapshot_approve(request, snapshot_id):
    snap = get_object_or_404(DataSnapshot, pk=snapshot_id)
    warnings = [c for c in snap.checks if c.get("level") == "warning"]
    if warnings and not request.data.get("warnings_read"):
        # Warnings often change pay (leave with no type counted as unpaid, a missing joining date): a person says
        # they read them before the data can be used.
        raise RuleViolation(f"This data has {len(warnings)} warning{'s' if len(warnings) != 1 else ''}. Read them and tick "
                            "\"I have read the warnings\" before marking the data as right.", code="warnings_not_read")
    snap = snapshot_service.approve(snap, request.user, request.data.get("note", ""))
    return Response(P.snapshot(snap))


@api_view(["POST"])
def snapshot_reject(request, snapshot_id):
    snap = snapshot_service.reject(get_object_or_404(DataSnapshot, pk=snapshot_id), request.user, request.data.get("note", ""))
    return Response(P.snapshot(snap))


@api_view(["GET"])
def agent_media(request, path):
    """Screenshots taken by the browser agent (signed-in users only)."""
    root = (Path(settings.MEDIA_ROOT) / "agent").resolve()
    target = (Path(settings.MEDIA_ROOT) / path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise Http404
    return FileResponse(open(target, "rb"), content_type="image/png")
