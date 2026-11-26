import json
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(settings, tmp_path):
    """Tests never call a real AI model, never need Temporal, and write files to a temp folder."""
    settings.AI_PROVIDER = "off"
    settings.WORKFLOW_BACKEND = "local"
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def statutory_versions():
    data = json.loads((REPO / "data/rules/statutory_in.json").read_text())["versions"]
    return [dict(v, id=str(i + 1), scope="statutory") for i, v in enumerate(data)]


def company_setting(key, payload, effective_from="2025-04-01", vid=None):
    return {"id": vid or f"c-{key}-{effective_from}", "key": key, "scope": "company", "jurisdiction": "",
            "effective_from": effective_from, "payload": payload, "title": key}


@pytest.fixture
def demo(db):
    """Seeded company with the Brickline spreadsheet imported and reviewed."""
    from payroll.demo import setup

    setup.ensure_users()
    setup.load_statutory_rules()
    seed = json.loads((REPO / "data/brickline/seed.json").read_text())
    c = seed["company"]
    company = setup.ensure_company(c["code"], c["name"], c["state"], c["city"])
    choices = json.loads((REPO / "answer_sheets/case_A/choices.json").read_text())
    setup.set_company_settings(company, choices["settings"], choices["effective_from"])
    snapshot = setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)
    return company, snapshot




@pytest.fixture
def northwind(db):
    """The onboarding sample company, brought in through the API the way the wizard does it: company, its
    day-count setting, its three HR files, reviewed data, and opening balances for April-September."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    from payroll.models import Company
    from payroll.demo import setup

    setup.ensure_users()
    setup.load_statutory_rules()
    folder = REPO / "data/northwind"
    seed = json.loads((folder / "seed.json").read_text())
    c = seed["company"]
    maker = Client()
    assert maker.post("/api/login", {"username": "maker", "password": "maker123"}, content_type="application/json").status_code == 200
    created = maker.post("/api/companies", {k: c[k] for k in ("name", "state", "city", "pan", "tan", "pf_establishment_code",
                                                               "esi_employer_code", "pay_day")}, content_type="application/json")
    assert created.status_code == 201, created.content
    cid = created.json()["id"]
    for key, payload in c["settings"].items():
        r = maker.post(f"/api/companies/{cid}/policies", {"key": key, "effective_from": "2025-04-01", "payload": payload},
                       content_type="application/json")
        assert r.status_code == 201, r.content
    files = {key: SimpleUploadedFile(name, (folder / name).read_bytes())
             for key, name in (("employees", "employees.xlsx"), ("compensation", "salaries.xlsx"), ("leave", "leave.csv"))}
    job = maker.post(f"/api/companies/{cid}/uploads", files).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"],
                                                                     "value_maps": t["mapping"]["value_maps"]},
                           content_type="application/json")
            assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [x for x in snap["checks"] if x["level"] == "error"], snap["checks"]
    assert maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type="application/json").status_code == 200
    ob = maker.post(f"/api/companies/{cid}/opening-balances", {"period": c["first_month"], "file": SimpleUploadedFile(
        "opening_balances.csv", (folder / "opening_balances.csv").read_bytes())})
    assert ob.status_code == 200, ob.content
    return Company.objects.get(pk=cid), seed, snap["id"]


def _chromium_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def hrms():
    if not _chromium_available():
        pytest.skip("Playwright Chromium is not installed")
    import importlib.util
    spec = importlib.util.spec_from_file_location("mock_hrms_app", REPO / "mock_hrms" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SEED.update(json.loads((REPO / "data/brickline/seed.json").read_text()))

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    server = make_server("127.0.0.1", 0, mod.app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", mod
    server.shutdown()
