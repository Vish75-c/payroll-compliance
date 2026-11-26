"""Demo HRMS: a small, deliberately different HR tool for exercising the browser agent.

It is *not* Horilla. It names fields differently ("Basic Salary", "House Rent Allowance",
"Special Allow."), lays out three screens three different ways (an HTML table with
pagination, an ARIA grid, and a plain div grid), and its sessions expire after a few page
views, so the agent has to log in again mid-read. Standard library only.

    python mock_hrms/app.py [--port 8100] [--seed data/brickline/seed.json]

Environment:
    MOCK_HRMS_SESSION_PAGES   page views before a session expires (default 8; 0 = never)
    MOCK_HRMS_VARIANT         "v1" (default), "v2" (renames/removes salary columns: a "changed screen") or "v3" (the nested
                              layout's "Employee Directory" tab is renamed "People": a confirmed click is gone)
    MOCK_HRMS_PAGE_SIZE       people per page (default 20)
    MOCK_HRMS_LAYOUT          "flat" (default: each list is one menu link away) or "nested" (like Keka: a menu item opens
                              a section, a tab inside it shows the list without changing the address, and an employee's
                              joining date, gender and location sit under a "Job" tab on their own page)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import secrets
from datetime import date
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
USERS = {"hr.admin": "demo123"}
SESSIONS: dict[str, dict] = {}
SEED: dict = {}
MONTH_ABBR = ["", "Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


def money(v) -> str:
    return f"₹{int(v):,}.00"


def long_date(iso: str) -> str:  # 01 May 2025
    d = date.fromisoformat(iso)
    return d.strftime("%d %b %Y")


def django_date(iso: str) -> str:  # Jul. 14, 2025
    d = date.fromisoformat(iso)
    return f"{MONTH_ABBR[d.month]} {d.day}, {d.year}"


STYLE = """
body{font-family:Georgia,serif;margin:0;background:#f4f1ea;color:#222}
header{background:#3b2f2f;color:#fff;padding:12px 20px;font-size:20px}
nav{float:left;width:190px;background:#e6dfd2;min-height:calc(100vh - 48px);padding:12px}
nav a{display:block;color:#3b2f2f;padding:6px 0;text-decoration:none}
main{margin-left:220px;padding:20px}
table{border-collapse:collapse;background:#fff}td,th{border:1px solid #ccc;padding:6px 10px}
.grid{display:table;background:#fff;border:1px solid #ccc}
.grid .r{display:table-row}.grid .c{display:table-cell;border-bottom:1px solid #ddd;padding:6px 10px}
.hdr .c{font-weight:bold;background:#eee}.pager a,.pager span{margin-right:10px}.muted{color:#777}
"""


def layout() -> str:
    return os.environ.get("MOCK_HRMS_LAYOUT", "flat")


TABS_SCRIPT = """<script>
function showTab(btn, url) {
  document.querySelectorAll('[role=tab]').forEach(b => b.setAttribute('aria-selected', 'false'));
  btn.setAttribute('aria-selected', 'true');
  const panel = document.getElementById('panel');
  if (!url) { panel.innerHTML = btn.dataset.text || ''; return; }
  fetch(url, {credentials: 'same-origin'}).then(r => r.text()).then(t => { panel.innerHTML = t; });
}
</script>"""


def tabs(items) -> str:
    """Tabs that change the content in place, with no address of their own (as single-page HR tools do)."""
    buttons = "".join(
        f"<button type='button' role='tab' aria-selected='false' data-text='{html.escape(text or '')}' "
        f"onclick=\"showTab(this, {repr(url) if url else 'null'})\">{html.escape(name)}</button> "
        for name, url, text in items)
    return f"<div role='tablist'>{buttons}</div><div id='panel' class='muted'>Choose a tab.</div>{TABS_SCRIPT}"


def page(title, body, user=None):
    nav = ""
    if user and layout() == "nested":
        nav = """<nav><b>Menu</b>
        <a href="/dashboard">Home</a>
        <a href="/org">Org</a>
        <a href="/time">Time</a>
        <a href="/payroll">Payroll</a>
        <a href="/logout">Log out</a></nav>"""
    elif user:
        nav = """<nav><b>Menu</b>
        <a href="/dashboard">Dashboard</a>
        <a href="/people">People Directory</a>
        <a href="/compensation">Pay Structures</a>
        <a href="/time-off">Time Off</a>
        <a href="/holidays">Holiday Calendar</a>
        <a href="/payslips">Payslip Archive</a>
        <a href="/logout">Log out</a></nav>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)} · Demo HRMS</title>
<style>{STYLE}</style></head><body><header>Demo HRMS — {html.escape(SEED['company']['name'])}</header>{nav}
<main><h1>{html.escape(title)}</h1>{body}</main></body></html>"""


def variant() -> str:
    return os.environ.get("MOCK_HRMS_VARIANT", "v1")


def session_limit() -> int:
    return int(os.environ.get("MOCK_HRMS_SESSION_PAGES", "8"))


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    query = parse_qs(environ.get("QUERY_STRING", ""))
    jar = cookies.SimpleCookie(environ.get("HTTP_COOKIE", ""))
    token = jar["hrms_session"].value if "hrms_session" in jar else None
    session = SESSIONS.get(token) if token else None

    def respond(status, body, headers=None):
        data = body.encode("utf-8")
        start_response(status, [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data)))] + (headers or []))
        return [data]

    def redirect(to, headers=None):
        start_response("302 Found", [("Location", to)] + (headers or []))
        return [b""]

    if path == "/login":
        error = ""
        if method == "POST":
            size = int(environ.get("CONTENT_LENGTH") or 0)
            form = parse_qs(environ["wsgi.input"].read(size).decode())
            user, pw = form.get("login_id", [""])[0], form.get("passcode", [""])[0]
            if USERS.get(user) == pw:
                new = secrets.token_hex(16)
                SESSIONS[new] = {"user": user, "views": 0}
                nxt = form.get("next", ["/dashboard"])[0] or "/dashboard"
                return redirect(nxt, [("Set-Cookie", f"hrms_session={new}; Path=/; HttpOnly")])
            error = "<p style='color:#b00'>Invalid credentials.</p>"
        nxt = html.escape(query.get("next", ["/dashboard"])[0])
        return respond("200 OK", page("Sign in", f"""{error}
<form method="post" action="/login"><input type="hidden" name="next" value="{nxt}">
<p><label>Login ID<br><input type="text" name="login_id" autocomplete="username"></label></p>
<p><label>Passcode<br><input type="password" name="passcode" autocomplete="current-password"></label></p>
<p><button type="submit">Enter</button></p></form><p class="muted">Demo login: hr.admin / demo123</p>"""))

    if path == "/logout":
        SESSIONS.pop(token, None)
        return redirect("/login")

    if session is None:
        target = path + ("?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
        return redirect("/login?next=" + quote(target))

    session["views"] += 1
    if session_limit() and session["views"] > session_limit():
        SESSIONS.pop(token, None)
        target = path + ("?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
        return redirect("/login?next=" + quote(target) + "&expired=1")

    user = session["user"]
    if path in ("/", "/dashboard"):
        return respond("200 OK", page("Dashboard", f"""<p>Welcome, {user}. {len(SEED['employees'])} people on the rolls.</p>
<p class="muted">Use the menu to see people, their pay structures and time off.</p>""", user))

    fragment = query.get("fragment", [""])[0] == "1"

    def show(title, body):
        return respond("200 OK", body if fragment else page(title, body, user))

    if layout() == "nested" and path == "/org":
        return show("Org", tabs([("Overview", None, f"{len(SEED['employees'])} people in the organisation."),
                                 ("People" if variant() == "v3" else "Employee Directory", "/people?fragment=1", None)]))
    if layout() == "nested" and path == "/time":
        reports = ("<ul><li><a href='/time/reports/leave-requests'>Employees Leave Requests</a></li>"
                   "<li><a href='/time/reports/balances'>Leave Balances</a></li></ul>")
        return show("Time", tabs([("Approvals", None, "No requests waiting for approval."), ("Reports", None, reports)]))
    if layout() == "nested" and path == "/payroll":
        return show("Payroll", tabs([("Run Payroll", None, "Payroll runs are not part of this demo."),
                                     ("Salary Structures", "/compensation?fragment=1", None)]))
    if layout() == "nested" and path == "/time/reports/balances":
        return show("Leave Balances", "<p class='muted'>Nothing here for this demo.</p>")

    if path == "/people":
        size = int(os.environ.get("MOCK_HRMS_PAGE_SIZE", "20"))
        people = SEED["employees"]
        total = len(people)
        pages = max(1, -(-total // size))
        n = min(max(1, int(query.get("page", ["1"])[0] or 1)), pages)
        chunk = people[(n - 1) * size: n * size]
        if layout() == "nested":  # the list shows little; the rest is on each person's page
            heads = "<th>Emp Code</th><th>Full Name</th><th>Team</th>"
            rows = "".join(f"<tr><td><a href='/people/{e['code']}'>{e['code']}</a></td><td>{html.escape(e['name'])}</td>"
                           f"<td>{html.escape(e['department'])}</td></tr>" for e in chunk)
        else:
            heads = "<th>Emp Code</th><th>Full Name</th><th>Gender</th><th>Location</th><th>Joined On</th><th>Last Working Day</th><th>Team</th>"
            rows = "".join(
                f"<tr><td><a href='/people/{e['code']}'>{e['code']}</a></td><td>{html.escape(e['name'])}</td>"
                f"<td>{'Female' if e['gender'] == 'F' else 'Male'}</td><td>{html.escape(e['location'])}</td>"
                f"<td>{long_date(e['doj'])}</td><td>{long_date(e['doe']) if e.get('doe') else ''}</td><td>{html.escape(e['department'])}</td></tr>" for e in chunk)
        prev = f"<a href='/people?page={n - 1}'>« Prev</a>" if n > 1 else "<span class='muted'>« Prev</span>"
        nxt = f"<a href='/people?page={n + 1}' rel='next'>Next ›</a>" if n < pages else "<span class='muted disabled'>Next ›</span>"
        first, last = (n - 1) * size + 1, (n - 1) * size + len(chunk)
        body = f"""<table><thead><tr>{heads}</tr></thead>
<tbody>{rows}</tbody></table><p>Showing {first}–{last} of {total} people</p><div class="pager">{prev} {nxt}</div>"""
        return show("People Directory", body)

    if path.startswith("/people/"):
        parts = path.strip("/").split("/")
        e = next((x for x in SEED["employees"] if x["code"] == parts[1]), None)
        if not e:
            return respond("404 Not Found", page("Not found", "<p>No such person.</p>", user))
        if len(parts) == 3 and parts[2] == "job":  # the Job tab's content, loaded when the tab is clicked
            fields = [("Gender", "Female" if e["gender"] == "F" else "Male"), ("Location", e["location"]),
                      ("Joined On", long_date(e["doj"])), ("Last Working Day", long_date(e["doe"]) if e.get("doe") else "")]
            return respond("200 OK", "".join(f"<div class='field'><span class='label'>{k}</span> <span class='value'>{html.escape(v)}</span></div>"
                                             for k, v in fields))
        about = f"""<dl><dt>Emp Code</dt><dd>{e['code']}</dd><dt>Designation</dt><dd>{html.escape(e['designation'])}</dd>
<dt>Team</dt><dd>{html.escape(e['department'])}</dd></dl>"""
        if layout() == "nested":
            return respond("200 OK", page(e["name"], about + tabs([("About", None, "Basic details are shown above."),
                                                                  ("Job", f"/people/{e['code']}/job", None)]), user))
        return respond("200 OK", page(e["name"], about, user))

    if path == "/compensation":
        v2 = variant() == "v2"
        heads = ["Emp Code", "Employee", "Basic Pay (₹)" if v2 else "Basic Salary", "House Rent Allowance", "Special Allow.",
                 "Effective Date"] + ([] if v2 else ["Approved On"])
        names = {e["code"]: e["name"] for e in SEED["employees"]}
        rows = []
        for s in SEED["salaries"]:
            cells = [s["code"], names[s["code"]], money(s["basic"]), money(s["hra"]), money(s["special"]), long_date(s["effective_from"])]
            if not v2:
                cells.append(long_date(s["recorded_on"]))
            rows.append("<div role='row'>" + "".join(f"<span role='cell'>{html.escape(c)} </span>" for c in cells) + "</div>")
        header = "<div role='row'>" + "".join(f"<b role='columnheader'>{h} </b>" for h in heads) + "</div>"
        body = f"""<p>Monthly pay components. A new row is added for every revision.</p>
<div role="table" aria-label="Pay structures" class="grid">{header}{''.join(rows)}</div>
<p>Total: {len(rows)} records</p>"""
        return show("Pay Structures", body)

    if path == "/time-off" or (layout() == "nested" and path == "/time/reports/leave-requests"):
        names = {e["code"]: e["name"] for e in SEED["employees"]}
        rows = "".join(
            f"<div class='r'><div class='c'>{lv['code']}</div><div class='c'>{html.escape(names[lv['code']])}</div>"
            f"<div class='c'>{html.escape(lv['type'])}</div><div class='c'>{django_date(lv['from'])}</div>"
            f"<div class='c'>{django_date(lv['to'])}</div><div class='c'>{lv['days']}</div><div class='c'>{lv['status']}</div></div>"
            for lv in SEED["leave"])
        header = "<div class='r hdr'>" + "".join(f"<div class='c'>{h}</div>" for h in
                                                ["Emp Code", "Name", "Leave Type", "Start", "End", "No. of Days", "Status"]) + "</div>"
        body = f"""<div class="grid"><div class="head">{header}</div><div class="body">{rows}</div></div>
<p>Total: {len(SEED['leave'])} requests</p>"""
        return show("Time Off", body)

    if path in ("/holidays", "/payslips"):
        return respond("200 OK", page(path.strip("/").title(), "<p class='muted'>Nothing here for this demo.</p>", user))

    return respond("404 Not Found", page("Not found", "<p>Page not found.</p>", user))


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get("MOCK_HRMS_LOG"):
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_HRMS_PORT", "8100")))
    parser.add_argument("--host", default=os.environ.get("MOCK_HRMS_HOST", "127.0.0.1"))
    parser.add_argument("--seed", default=str(ROOT / "data" / "brickline" / "seed.json"))
    args = parser.parse_args()
    SEED.update(json.loads(Path(args.seed).read_text()))
    print(f"Demo HRMS on http://{args.host}:{args.port}  (login hr.admin / demo123, variant {variant()}, "
          f"session expires after {session_limit() or 'no'} page views)", flush=True)
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer

    class ThreadingServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    make_server(args.host, args.port, app, server_class=ThreadingServer, handler_class=QuietHandler).serve_forever()


if __name__ == "__main__":
    main()
