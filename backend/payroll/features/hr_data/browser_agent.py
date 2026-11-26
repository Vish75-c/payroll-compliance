"""A browser agent that reads an HR tool's screens the way a person would. Works with any HR tool.

* logs in by finding the password box on the login page (no tool-specific selectors);
* goes to the screens a person confirmed (first found by the AI or by keywords);
* reads whatever the screen shows as a table: HTML tables, ARIA grids, or repeated
  div rows (as used by many modern HR tools, including Horilla);
* follows "Next" pages until there are none, and compares the rows read with any
  "Showing 1 to 10 of 23" count on screen;
* if the session expires mid-read it logs in again and carries on from the same page;
* if a screen has no recognisable table it asks the AI to read the visible text, and keeps
  only values that literally appear on the screen; otherwise it reports the screen as changed.
Every page visited is screenshotted for the audit trail.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from django.conf import settings
from django.db import close_old_connections

from ..ai import client as ai_client
from ..ai import tasks as ai_tasks
from . import profiles
from .canonical import IGNORE, heuristic_mapping

DATASETS = {"employees": "Employees", "compensation": "Salary", "leave": "Leave"}

# Status filters of a list screen's filter form (a multi-select such as Horilla's contract_status, pre-set to
# Draft + Active). Only selects inside a <form> count: a per-row status select edits that record, and the
# agent never touches it. The agent never changes or submits anything; it only opens the same address with
# every status as a query parameter.
STATUS_FILTERS_JS = """() => [...document.querySelectorAll('form select[multiple]')]
  .filter(s => /status|state/i.test(s.name || '') && s.options.length > 1)
  .map(s => ({name: s.name,
              all: [...s.options].map(o => ({value: o.value, text: o.text.trim()})).filter(o => o.value !== ''),
              selected: [...s.options].filter(o => o.selected && o.value !== '').map(o => o.value)}))"""
ROW_COUNT_JS = "() => document.querySelectorAll('table tbody tr, [role=row]').length"


def off_site(url: str, base_url: str) -> str | None:
    """The other site's address when ``url`` is not on the HR tool at ``base_url``, else None.

    The agent never opens, or signs in on, another site: a screen address pointing elsewhere is
    a mistake (or a trap), and following it could hand the saved login to someone else."""
    home = urlparse(base_url)
    target = urlparse(urljoin(base_url.rstrip("/") + "/", url or ""))
    if (target.scheme, target.netloc.lower()) != (home.scheme, home.netloc.lower()):
        return f"{target.scheme}://{target.netloc}" if target.netloc else url
    return None


class AgentError(Exception):
    pass


class PathBroken(Exception):
    """A saved click is not on the screen any more: the HR tool's screens changed and the path must be learned again."""


EXTRACT_JS = r"""
() => {
  const txt = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };
  // A row's own page: its first real link, or, for rows that open a page when clicked, the address in that click.
  const firstLink = row => {
    const a = row.querySelector('a[href]');
    if (a && !a.getAttribute('href').startsWith('#') && !a.getAttribute('href').startsWith('javascript')) return a.href;
    const said = row.getAttribute('data-href') || row.getAttribute('data-url')
      || ((row.getAttribute('onclick') || '').match(/(?:location(?:\.href)?\s*=|location\.assign\(|window\.open\()\s*['"]([^'"]+)['"]/) || [])[1];
    return said ? new URL(said, location.href).href : null;
  };
  const found = [];
  const claimed = new Set();

  // 1. Real HTML tables
  for (const t of document.querySelectorAll('table')) {
    if (!visible(t)) continue;
    let headerCells = [...t.querySelectorAll(':scope > thead > tr:last-child > th, :scope > thead > tr:last-child > td')];
    let bodyRows = [...t.querySelectorAll(':scope > tbody > tr, :scope > tr')];
    if (!headerCells.length && bodyRows.length) {
      headerCells = [...bodyRows[0].children];
      bodyRows = bodyRows.slice(1);
    }
    const headers = headerCells.map(txt);
    if (headers.filter(Boolean).length < 2) continue;
    const rows = [], links = [];
    for (const tr of bodyRows) {
      if (!visible(tr)) continue;
      const cells = [...tr.children].filter(c => c.tagName === 'TD' || c.tagName === 'TH');
      if (!cells.length || (cells.length === 1 && cells[0].colSpan > 1)) continue;
      rows.push(cells.map(txt)); links.push(firstLink(tr));
    }
    found.push({method: 'html table', headers, rows, links});
    claimed.add(t);
  }

  // 2. ARIA grids
  for (const g of document.querySelectorAll('[role=table], [role=grid]')) {
    if (g.tagName === 'TABLE' || !visible(g)) continue;
    const headers = [...g.querySelectorAll('[role=columnheader]')].map(txt);
    if (headers.filter(Boolean).length < 2) continue;
    const rows = [], links = [];
    for (const r of g.querySelectorAll('[role=row]')) {
      if (r.querySelector('[role=columnheader]') || !visible(r)) continue;
      const cells = [...r.querySelectorAll('[role=cell], [role=gridcell]')];
      if (!cells.length) continue;
      rows.push(cells.map(txt)); links.push(firstLink(r));
    }
    found.push({method: 'aria grid', headers, rows, links});
    claimed.add(g);
  }

  // 3. Repeated div rows: a container whose children share one shape (same tag + classes, same number of cells)
  const sig = el => el.tagName + '.' + [...el.classList].sort().join('.');
  const isClaimed = el => [...claimed].some(c => c.contains(el) || el.contains(c));
  const candidates = [];
  for (const box of document.querySelectorAll('body *')) {
    if (box.children.length < 1 || isClaimed(box) || !visible(box)) continue;
    const groups = {};
    for (const ch of box.children) { const s = sig(ch); (groups[s] = groups[s] || []).push(ch); }
    for (const [s, members] of Object.entries(groups)) {
      const counts = {};
      for (const m of members) counts[m.children.length] = (counts[m.children.length] || 0) + 1;
      const [cellCount, howMany] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
      const n = Number(cellCount);
      if (n < 2 || howMany < Math.max(1, members.length * 0.8)) continue;
      const rows = members.filter(m => m.children.length === n && visible(m));
      if (!rows.length) continue;
      if (rows.filter(r => [...r.children].filter(c => txt(c)).length >= 2).length < rows.length * 0.8) continue;
      // header: an earlier sibling (or a sibling of the parent) with the same number of text cells
      let header = null;
      const lookIn = [];
      for (const ch of box.children) { if (ch === rows[0]) break; lookIn.push(ch); }
      let prev = box.previousElementSibling;
      for (let i = 0; prev && i < 3; i++, prev = prev.previousElementSibling) lookIn.push(prev);
      for (const h of lookIn) {
        const options = [h, ...h.querySelectorAll('*')];
        for (const o of options) {
          if (o.children.length === n && rows.indexOf(o) < 0 && [...o.children].filter(c => txt(c)).length >= Math.min(2, n)) { header = o; break; }
        }
        if (header) break;
      }
      if (!header && rows.length < 2) continue;
      if (rows.length < 2 && n < 3) continue;
      let headers = header ? [...header.children].map(txt) : rows[0] && [...rows[0].children].map(txt);
      let data = header ? rows : rows.slice(1);
      candidates.push({box, score: data.length * n + (header ? 5 : 0), headers, rows: data.map(r => [...r.children].map(txt)), links: data.map(firstLink)});
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  const chosen = [];
  for (const c of candidates) {
    if (chosen.some(x => x.box.contains(c.box) || c.box.contains(x.box))) continue;
    if (c.headers.filter(Boolean).length < 2) continue;
    chosen.push(c);
    if (chosen.length >= 3) break;
  }
  for (const c of chosen) found.push({method: 'repeated rows', headers: c.headers, rows: c.rows, links: c.links});

  // Pagination: a visible, enabled "next" control: a word, a label, or a lone arrow icon
  // (many HR tools draw "next" as a chevron with no text; "last page" double arrows are not "next").
  let next = null;
  const nextWords = ['next', 'next ›', 'next »', '›', '»', '>', 'next page', '→', '>>'];
  // An arrow counts only inside the page controls: a pagination bar, or next to a page-number box.
  const inPager = el => !!el.closest('[class*=paginat i], [id*=paginat i], [class*=pager i], nav[aria-label*=page i]')
    || (el.parentElement && !['BODY', 'MAIN', 'HTML'].includes(el.parentElement.tagName)
        && !!el.parentElement.querySelector(':scope > input[name=page], :scope > input[name*=page i][type=number], :scope > * > input[name=page]'));
  const arrowIcon = el => !txt(el) && inPager(el) && [el, ...el.querySelectorAll('i, svg, span, use')].some(i => {
    const c = ((i.getAttribute && (i.getAttribute('class') || '')) + ' ' + (i.getAttribute && (i.getAttribute('href') || i.getAttribute('data-icon') || ''))).toLowerCase();
    return /(angle|chevron|arrow|caret)[-_ ]?(right|forward|next)\b/.test(c) && !/(angles|double|last|end)/.test(c);
  });
  for (const el of document.querySelectorAll('a, button, [role=button]')) {
    if (!visible(el)) continue;
    // A guided tour or pop-up has its own "Next" button; that is not the list's next page.
    if (el.closest('[class*=driver-popover], [class*=tour i], [class*=intro i], [class*=shepherd i], [role=dialog], [class*=modal i], [class*=popover i]')) continue;
    const label = (txt(el) || el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase().trim();
    const isNext = el.getAttribute('rel') === 'next' || nextWords.includes(label) || /\bnext\b/.test((el.getAttribute('aria-label') || '').toLowerCase())
      || /\bnext\b/.test((el.getAttribute('title') || '').toLowerCase()) || arrowIcon(el);
    if (!isNext) continue;
    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || /disabled|not-allowed/.test(el.className) || (el.parentElement && /disabled/.test(el.parentElement.className));
    if (disabled) continue;
    el.setAttribute('data-agent-next', '1');
    const href = el.tagName === 'A' ? el.getAttribute('href') : null;
    next = {href: href && !href.startsWith('#') && !href.startsWith('javascript') ? el.href : null};
    break;
  }
  // How many pages the screen says there are: a page-number box with a maximum, or "Page 2 of 4".
  let pages = null;
  const box = [...document.querySelectorAll('input[name=page][max], input[type=number][name*=page i][max]')].find(visible);
  if (box && Number(box.max) > 0) pages = {current: Number(box.value) || null, total: Number(box.max)};
  else {
    const m = (document.body.innerText || '').match(/\bpage\s+(\d+)\s+(?:of|\/)\s+(\d+)\b/i);
    if (m) pages = {current: Number(m[1]), total: Number(m[2])};
  }
  return {url: location.href, title: document.title, tables: found, next, pages, text: (document.body.innerText || '').slice(0, 200000)};
}
"""

# Close what a person would close before carrying on: a guided product tour, a welcome pop-up, a cookie banner.
DISMISS_JS = r"""
() => {
  const words = /^(skip( tour)?|close|got it|dismiss|no thanks|not now|done|end tour|×|✕|x)$/i;
  const holders = '[class*=driver i], [class*=tour i], [class*=intro i], [class*=shepherd i], [class*=joyride i], [role=dialog], [class*=modal i], [class*=popover i], [class*=overlay i], [class*=cookie i]';
  let closed = 0;
  for (const el of document.querySelectorAll('button, a, [role=button]')) {
    if (!el.closest(holders)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const label = ((el.innerText || '') .trim() || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    if (words.test(label) || /close|dismiss/i.test(el.getAttribute('aria-label') || '') || /close/i.test(el.className || '')) { el.click(); closed++; }
  }
  return closed;
}
"""

LINKS_JS = r"""
() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript') || href.startsWith('mailto')) continue;
    const url = a.href;
    if (new URL(url).origin !== location.origin) continue;
    const text = ((a.innerText || a.textContent || a.getAttribute('title') || a.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim();
    if (/log ?out|sign ?out|delete|remove/i.test(text + ' ' + url)) continue;
    if (!seen.has(url) || (text && !seen.get(url))) seen.set(url, text);
  }
  return [...seen.entries()].map(([url, text]) => ({url, text}));
}
"""

# Everything a person could click to move around: links, buttons, tabs, menu items. Each gets a number for the AI to
# pick, and is saved by its role and visible words (not its position), so a saved path still works when the layout
# shifts. Anything that could change data (save, delete, approve, submit…) and buttons inside forms are never listed:
# the agent only reads. With an argument {role, text}, the same rules find that one element again and mark it, so the
# agent clicks exactly what it would have listed (learning and replaying use one definition).
CLICKABLES_JS = r"""
(want) => {
  const NEVER = /log ?out|sign ?out|delete|remove|save|submit|approve|reject|cancel|apply|run payroll|lock|send|pay now|create|add |new |edit|update|upload|import/i;
  const words = el => (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
  const roleOf = el => el.getAttribute('role') || (el.tagName === 'A' ? 'link' : el.tagName === 'SUMMARY' ? 'button' : el.tagName.toLowerCase());
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [], seen = new Set();
  document.querySelectorAll('[data-agent-click]').forEach(el => el.removeAttribute('data-agent-click'));
  for (const el of document.querySelectorAll('a[href], button, summary, [role=tab], [role=menuitem], [role=link], [role=button], [onclick]')) {
    if (!visible(el)) continue;
    const role = roleOf(el), text = words(el).slice(0, 80);
    if (!text || NEVER.test(text)) continue;
    if (el.closest('form') && role !== 'tab' && role !== 'link') continue;
    if (el.tagName === 'A') {
      const href = el.getAttribute('href') || '';
      if (href.startsWith('mailto') || href.startsWith('tel')) continue;
      if (!href.startsWith('#') && !href.startsWith('javascript') && new URL(el.href).origin !== location.origin) continue;
    }
    const key = role + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    if (want && want.role === role && want.text === text) { el.setAttribute('data-agent-click', '1'); return [{role, text}]; }
    out.push({n: out.length, role, text, url: el.tagName === 'A' && !(el.getAttribute('href') || '').startsWith('#') ? el.href : null});
    if (out.length >= 150) break;
  }
  return want ? [] : out;
}
"""

# Label and value pairs on a record's own page, however they are laid out: <dl>, two-cell rows, <label for>, and the
# div layouts most HR tools use (a short label element followed by its value).
FIELDS_JS = r"""() => {
  const out = {};
  const t = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const put = (k, v) => { k = k.replace(/:$/, '').trim(); if (k && k.length < 60 && !(k in out)) out[k] = v; };
  document.querySelectorAll('dl').forEach(dl => dl.querySelectorAll('dt').forEach(dt => { const dd = dt.nextElementSibling; if (dd && dd.tagName === 'DD') put(t(dt), t(dd)); }));
  document.querySelectorAll('tr').forEach(tr => { const c = tr.children; if (c.length === 2 && t(c[0]) && t(c[0]).length < 60) put(t(c[0]), t(c[1])); });
  document.querySelectorAll('label[for]').forEach(l => { const i = document.getElementById(l.getAttribute('for')); if (i && 'value' in i && i.value) put(t(l), i.value); });
  document.querySelectorAll('label, strong, b, span, div, p, th').forEach(el => {
    if (el.children.length || el.closest('nav, header, [role=tablist], dl, table')) return;
    const label = t(el), next = el.nextElementSibling;
    const looksLikeLabel = /:$/.test(label) || /label|key|title|caption/i.test(el.className || '') || ['LABEL', 'STRONG', 'B'].includes(el.tagName);
    if (!looksLikeLabel || !next || next.children.length || !t(next) || t(next).length > 200 || label.length > 40) return;
    put(label, t(next));
  });
  return out;
}"""

# What the agent is looking for on each kind of screen, in the words the AI is given.
GOALS = {
    "employees": "the list of every employee (one row per person, with their name or employee ID)",
    "compensation": "every employee's salary or pay structure (contracts, compensation, salary components)",
    "leave": "every employee's leave records or leave requests",
}
MAX_LEARN_STEPS = 12

TOTAL_PATTERNS = [
    re.compile(r"(?i)\b\d[\d,]*\s*(?:-|–|to)\s*\d[\d,]*\s+of\s+(\d[\d,]*)\b"),
    re.compile(r"(?i)\btotal\s*(?:records|entries|employees|rows|count)?\s*[:=]\s*(\d[\d,]*)\b"),
]


def total_shown(text: str) -> int | None:
    for pattern in TOTAL_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


class BrowserAgent:
    def __init__(self, connector, log, job=None):
        self.connector = connector
        self.company = connector.company  # loaded now: no database access once the browser runs
        self.job = job
        # Playwright's sync API runs an event loop in this thread, and Django refuses database
        # work inside one. Logging and AI calls (which touch the database) run on a helper thread.
        self._db_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-db")
        self.log = lambda *a, **k: self._db(log, *a, **k)
        self.pages_visited = 0
        self.relogins = 0
        self.max_relogins = 5
        self.screens_dir = Path(settings.MEDIA_ROOT) / "agent" / f"job-{job.pk if job else 'discovery'}"
        self.screens_dir.mkdir(parents=True, exist_ok=True)

    def _db(self, fn, *args, **kwargs):
        return self._db_pool.submit(fn, *args, **kwargs).result()

    def shutdown(self):
        try:
            self._db_pool.submit(close_old_connections).result()
        finally:
            self._db_pool.shutdown(wait=True)

    # ------------------------------------------------------------------ browser plumbing
    def _browser(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._b = self._pw.chromium.launch(headless=settings.AGENT_HEADLESS)
        except Exception as exc:  # noqa: BLE001
            self._pw.stop()
            raise AgentError(f"Could not start the browser ({exc}). Run 'playwright install chromium'.") from exc
        context = self._b.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(settings.AGENT_NAV_TIMEOUT_MS)
        return page

    def _close(self):
        try:
            if getattr(self, "_b", None):
                self._b.close()
        except Exception:  # noqa: BLE001 - closing a crashed browser must not hide the real error
            pass
        try:
            if getattr(self, "_pw", None):
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass

    def _url(self, path: str) -> str:
        return urljoin(self.connector.base_url.rstrip("/") + "/", path)

    def _settle(self, page):
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001 - some apps keep a socket open; the DOM is still usable
            pass
        page.wait_for_timeout(300)

    def _goto(self, page, url: str):
        from playwright.sync_api import Error as PlaywrightError

        try:
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise AgentError(f"Could not open {url}: {exc.message.splitlines()[0]}") from exc
        self._settle(page)
        self.pages_visited += 1

    def _screenshot(self, page, label: str) -> str:
        name = f"{self.pages_visited:03d}-{re.sub(r'[^a-z0-9]+', '-', label.lower())[:40]}.png"
        try:
            page.screenshot(path=str(self.screens_dir / name), full_page=True)
        except Exception:  # noqa: BLE001
            return ""
        return str((self.screens_dir / name).relative_to(settings.MEDIA_ROOT))

    @staticmethod
    def is_login_page(page) -> bool:
        try:
            return page.locator("input[type=password]:visible").count() > 0
        except Exception:  # noqa: BLE001
            return False

    def login(self, page):
        login_url = self.connector.recipe.get("login_url") or self.connector.base_url
        if other := off_site(login_url, self.connector.base_url):
            raise AgentError(f"The login page is on {other}, not on {self.connector.base_url}; the agent signs in only on the HR tool itself.")
        self._goto(page, self._url(login_url))
        if not self.is_login_page(page):
            self.log("Already signed in.")
            return
        if not self.connector.username or not self.connector.password:
            raise AgentError("The HR tool asks for a login, but no username/password is saved for it.")
        password = page.locator("input[type=password]:visible").first
        form = password.locator("xpath=ancestor::form[1]")
        scope = form if form.count() else page
        user_box = scope.locator(
            "input[type=email]:visible, input[type=text]:visible, input:not([type]):visible, input[name*=user i]:visible, input[name*=login i]:visible"
        ).first
        if user_box.count() == 0:
            raise AgentError("Found a password box but no username box on the login page.")
        user_box.fill(self.connector.username)
        password.fill(self.connector.password)
        password.press("Enter")
        self._settle(page)
        if self.is_login_page(page):
            raise AgentError("Login failed: the HR tool did not accept the saved username and password.")
        self.log(f"Signed in to {self.connector.system_name} as {self.connector.username}.")

    def _wait_for_new_rows(self, page, dataset: str, previous: str, seconds: float = 10.0):
        """After a next-page click, many apps replace the rows a moment after their request finishes.
        Wait until the rows differ from the page just read (or give up after a while; the caller then sees the
        same rows and stops, flagged)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            probe = page.evaluate(EXTRACT_JS)
            table = self._pick_table(probe["tables"], dataset)
            if table and hashlib.sha256(json.dumps(table["rows"]).encode()).hexdigest() != previous:
                return
            page.wait_for_timeout(300)

    def _click_next(self, page) -> bool:
        """Click the list's next-page control the way a person would: if something covers it (a guided tour,
        a pop-up), close that first. As a last resort press the control directly, and say so."""
        target = page.locator("[data-agent-next='1']").first
        for attempt in range(3):
            try:
                target.click(timeout=5000)
                return True
            except Exception:  # noqa: BLE001 - covered or not ready; try to clear the way
                if attempt == 0:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                else:
                    try:
                        closed = page.evaluate(DISMISS_JS)
                    except Exception:  # noqa: BLE001
                        closed = 0
                    if closed:
                        self.log("Closed a pop-up (such as a guided tour) that covered the page.")
                    page.wait_for_timeout(500)
        try:
            target.evaluate("el => el.click()")
            self.log("Something kept covering the next-page control, so it was pressed directly.", "warning")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _ensure_logged_in(self, page, target_url: str):
        if not self.is_login_page(page):
            return False
        self.relogins += 1
        if self.relogins > self.max_relogins:
            raise AgentError("The session keeps expiring; stopped after several new logins.")
        self.log("The session expired (the login page came back). Signing in again and resuming from the same page.", "warning")
        self.login(page)
        self._goto(page, target_url)
        return True

    # ------------------------------------------------------------------ paths: saved clicks
    def _click_step(self, page, step: dict):
        """Click one saved step, found again by its role and words. Learning and replaying both come through here."""
        found = page.evaluate(CLICKABLES_JS, {"role": step["role"], "text": step["text"]})
        if not found:
            raise PathBroken(f"'{step['text']}' ({step['role']}) is not on the screen any more ({page.url}).")
        before = self._screen_text(page)
        try:
            page.locator("[data-agent-click='1']").first.click(timeout=10000)
        except Exception as exc:  # noqa: BLE001 - covered or gone: same meaning as not found
            raise PathBroken(f"'{step['text']}' ({step['role']}) could not be clicked on {page.url}.") from exc
        self._settle(page)
        self._wait_until_still(page, before)
        self.pages_visited += 1

    @staticmethod
    def _screen_text(page) -> str:
        try:
            return page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:  # noqa: BLE001 - mid-navigation
            return ""

    def _wait_until_still(self, page, before: str, seconds: float = 8.0):
        """A tab or button often loads its content in the background after the click, when the page already counts
        as loaded. Wait until the screen has changed and then stays the same for a moment (or give up after a while)."""
        deadline = time.monotonic() + seconds
        last, still_since = before, None
        while time.monotonic() < deadline:
            now = self._screen_text(page)
            if now != last:
                last, still_since = now, time.monotonic()
            elif now != before and still_since and time.monotonic() - still_since > 0.4:
                return
            page.wait_for_timeout(150)

    def _follow_path(self, page, spec: dict, label: str):
        """Open the screen's start address and replay its confirmed clicks. If the session expires on the way,
        sign in again and replay from the start."""
        start = self._url(spec["url"])
        for _ in range(self.max_relogins + 1):
            self._goto(page, start)
            self._ensure_logged_in(page, start)
            expired = False
            for step in spec.get("steps") or []:
                self._click_step(page, step)
                if self.is_login_page(page):
                    expired = True
                    break
            if not expired:
                if spec.get("steps"):
                    self.log(f"{label}: followed the path " + " → ".join(s["text"] for s in spec["steps"]) + ".")
                return
            self._ensure_logged_in(page, start)
        raise AgentError("The session keeps expiring; stopped after several new logins.")

    def _list_here(self, page, dataset: str) -> dict | None:
        """The table on this screen if it is the list being looked for, judged by its column names (plain code, not AI)."""
        tables = page.evaluate(EXTRACT_JS)["tables"]
        table = self._pick_table(tables, dataset)
        if table is None:
            return None
        guess = heuristic_mapping([h for h in table["headers"] if h], "")
        groups = [v["target"].split(".")[0] for v in guess.values() if v["target"] != IGNORE]
        targets = {v["target"] for v in guess.values() if v["target"] != IGNORE}
        if dataset == "employees":
            people = {"employee.code", "employee.name"} & targets
            other = [t for t in targets if t.startswith("employee.") and t not in people]
            ok = bool(people) and (other or len(people) == 2) and groups.count("comp") + groups.count("leave") == 0
        elif dataset == "compensation":
            ok = groups.count("comp") + groups.count("revision") >= 1
        else:
            ok = groups.count("leave") >= 2
        return table if ok else None

    def learn_path(self, page, dataset: str, home: str) -> dict | None:
        """Find a list one click at a time. On each screen the AI (or the keyword matcher) picks what to click from
        what is really there; plain code decides when the list has been reached. Returns the screen spec to confirm:
        a start address and the clicks after it, each with a screenshot of the screen it was made on."""
        label = DATASETS.get(dataset, dataset)
        self.log(f"Looking for the {label} list, one click at a time from the home page.")
        self._goto(page, home)
        self._ensure_logged_in(page, home)
        steps, tried = [], set()
        for i in range(MAX_LEARN_STEPS + 1):
            table = self._list_here(page, dataset)
            if table is not None:
                return self._finish_path(page, dataset, home, steps, table)
            if i == MAX_LEARN_STEPS:
                break
            options = [o for o in page.evaluate(CLICKABLES_JS, None) if (o["role"], o["text"]) not in tried]
            if not options:
                break
            interaction = self._db(ai_tasks.next_click, company=self.company, dataset=dataset, goal=GOALS[dataset],
                                   url=page.url, title=page.title(), options=options, path=[s["text"] for s in steps],
                                   page_text=page.inner_text("body"))
            answer = interaction.response or {}
            choice = next((o for o in options if o["n"] == answer.get("click")), None)
            if choice is None or any(c.get("level") == "error" for c in interaction.validation):
                self.log(f"{label}: nothing on {page.url} leads further ({answer.get('reason') or 'no answer'}).", "warning")
                break
            step = {"action": "click", "role": choice["role"], "text": choice["text"], "reason": answer.get("reason", ""),
                    "on": page.url, "screenshot": self._screenshot(page, f"learn-{dataset}-{i + 1}")}
            if choice.get("url"):
                step["url"] = choice["url"]
            self.log(f"{label}, step {i + 1}: clicking '{choice['text']}' ({choice['role']}). Why: {step['reason'].rstrip('.')}.")
            tried.add((choice["role"], choice["text"]))
            self._click_step(page, step)
            self._ensure_logged_in(page, page.url)
            steps.append(step)
        self.log(f"Could not find the {label} list within {MAX_LEARN_STEPS} clicks. Enter its screen by hand.", "warning")
        return None

    def _finish_path(self, page, dataset: str, home: str, steps: list[dict], table: dict) -> dict:
        """Keep the shortest path that still works: if the list has its own address (the last click that went to a
        new page), start there; otherwise keep every click from the home page. Checked by opening it again."""
        label = DATASETS.get(dataset, dataset)
        for cut in range(len(steps), 0, -1):
            step = steps[cut - 1]
            if not step.get("url"):
                continue  # a tab or button: no address of its own
            start, rest = step["url"], steps[cut:]
            if off_site(start, self.connector.base_url):
                break
            try:
                self._follow_path(page, {"url": start, "steps": rest}, label)
            except PathBroken:
                break
            if self._list_here(page, dataset) is not None:
                if not rest:
                    self.log(f"{label}: the list has its own address, {start}; reads go straight there.")
                return self._path_spec(start, rest, steps, table)
            break  # opening that address directly does not show the list: keep every click from the home page
        return self._path_spec(home, steps, steps, table)

    @staticmethod
    def _path_spec(start: str, rest: list[dict], all_steps: list[dict], table: dict) -> dict:
        how = " → ".join(s["text"] for s in all_steps) or "the home page"
        parts = urlparse(start)
        address = urlunparse(parts._replace(scheme="", netloc="")) or "/"
        spec = {"url": address, "reason": f"found by clicking {how} ({len(table['rows'])} rows on the first page)",
                "found_by": "clicks", "learned_path": [{k: s[k] for k in ("role", "text", "reason", "screenshot") if k in s} for s in all_steps]}
        if rest:
            spec["steps"] = [{k: s[k] for k in ("action", "role", "text") if k in s} for s in rest]
        return spec

    def learn_detail(self, page, table: dict) -> dict:
        """Open the first record's own page, click each tab on it and keep the tabs that show more fields. Plain code,
        no AI: a tab either adds label/value pairs or it does not."""
        link = next((u for u in table.get("links") or [] if u and not off_site(u, self.connector.base_url)), None)
        if not link:
            return {}
        self._goto(page, link)
        self._ensure_logged_in(page, link)
        fields = set(page.evaluate(FIELDS_JS))
        keep = []
        for tab in [o for o in page.evaluate(CLICKABLES_JS, None) if o["role"] == "tab"]:
            step = {"action": "click", "role": "tab", "text": tab["text"]}
            try:
                self._click_step(page, step)
            except PathBroken:
                continue
            new = set(page.evaluate(FIELDS_JS)) - fields
            if new:
                keep.append(step)
                fields |= new
                self.log(f"Employee pages: the '{tab['text']}' tab shows {', '.join(sorted(new))}.")
        guess = heuristic_mapping(sorted(fields), "employees")
        in_list = {h for h in table.get("headers", [])}
        useful = sorted(f for f in fields if f not in in_list and guess[f]["target"].startswith("employee.")
                        and guess[f]["target"] not in ("employee.code", "employee.name"))
        if useful:
            self.log(f"Each employee's own page adds {', '.join(useful)}" + (f", after clicking {', '.join(s['text'] for s in keep)}" if keep else "")
                     + ". Suggesting to read every employee's page.")
        return {"follow_row_links": bool(useful), "detail_steps": keep, "detail_fields": useful}

    # ------------------------------------------------------------------ discovery
    def discover(self, company, force=False) -> dict:
        """Sign in, look at the menu and suggest the screens: from the HR tool's profile when the tool is known
        (chosen for the connection, or recognised from the page), otherwise by asking the AI or the rule-based
        matcher. A suggested screen that does not show the list itself is followed further, one click at a time
        (``learn_path``), and an employee's own page is checked for tabs with more fields (``learn_detail``).
        Returns {"datasets": {key: spec | None}, "profile", "interaction", "links"}."""
        page = self._browser()
        try:
            self.login(page)
            home = page.url
            links = page.evaluate(LINKS_JS)
            title = page.title()
            text = page.inner_text("body")[:5000]
            self.log(f"Found {len(links)} links on '{title}'.")
            known = profiles.profile_for(self.connector.profile) or profiles.detect_system(title, text)
            if known and known.screens.get("datasets"):
                self.log(f"This is {known.name}: using the screens from the {known.name} profile (no AI).")
                screens = profiles.screens_for(known)
                return {"datasets": {k: screens.get(k) for k in DATASETS}, "profile": known, "interaction": None, "links": links}
            interaction = self._db(ai_tasks.discover_pages, company=company, system_name=self.connector.system_name,
                                   page_title=title, links=links, force=force)
            suggestion = interaction.response or {}
            datasets = {}
            for key, label in DATASETS.items():
                pick = suggestion.get(key)
                table = None
                if pick and pick.get("url") and not off_site(pick["url"], self.connector.base_url):
                    self._goto(page, pick["url"])
                    self._ensure_logged_in(page, pick["url"])
                    table = self._list_here(page, key)
                    if table is None:
                        self.log(f"The {label} link ({pick['url']}) opens a screen without the list itself; looking further.")
                if table is not None:
                    datasets[key] = {**pick, "found_by": "menu link"}
                else:
                    datasets[key] = self.learn_path(page, key, home)
                    if datasets[key]:
                        try:  # replay it once now, exactly as a read will
                            self._follow_path(page, datasets[key], label)
                            table = self._list_here(page, key)
                        except PathBroken as exc:
                            self.log(f"{label}: the path found could not be followed again ({exc}).", "warning")
                            datasets[key], table = None, None
                if key == "employees" and datasets[key] and table is not None:
                    datasets[key].update(self.learn_detail(page, table))
            return {"datasets": datasets, "profile": None, "interaction": interaction, "links": links}
        finally:
            self._close()
            self.shutdown()

    # ------------------------------------------------------------------ reading
    def read_all(self) -> tuple[list[dict], list[dict]]:
        page = self._browser()
        checks: list[dict] = []
        tables = []
        try:
            self.login(page)
            for dataset, spec in self.connector.recipe.get("datasets", {}).items():
                if not spec or not spec.get("url"):
                    continue
                if other := off_site(spec["url"], self.connector.base_url):
                    self.log(f"Not opening the {DATASETS.get(dataset, dataset)} screen: {spec['url']} is on {other}, "
                             f"not on {self.connector.system_name} ({self.connector.base_url}).", "error")
                    checks.append({"level": "error", "code": "screen_off_site",
                                   "message": f"{DATASETS.get(dataset, dataset)} screen: {spec['url']} is on another site ({other}), "
                                              f"so it was not opened. Fix the screen address for {self.connector.name}."})
                    continue
                label = DATASETS.get(dataset, dataset)
                try:
                    table = self.read_dataset(page, dataset, spec)
                except PathBroken as exc:
                    path = " → ".join(s["text"] for s in spec.get("steps") or [])
                    self.log(f"{label}: the confirmed path ({path}) no longer works: {exc} Stopped reading this screen.", "error")
                    checks.append({"level": "error", "code": "path_broken",
                                   "message": f"{label} screen changed: {exc} The confirmed path was {path}. Find the screens "
                                              f"again (HR data → Find screens again) and confirm the new path."})
                    continue
                if table is None:
                    checks.append({"level": "error", "code": "screen_unreadable",
                                   "message": f"{label} screen: no table could be read at {spec['url']}. The screen may have changed."})
                    continue
                tables.append(table)
                if spec.get("follow_row_links"):
                    self.read_details(page, table, spec, checks)
        finally:
            self._close()
            self.shutdown()
        if not tables:
            raise AgentError("No data could be read from any screen. Check the confirmed screens and the login.")
        return tables, checks

    def _prepare(self, page, dataset: str, spec: dict):
        """Open the addresses a known HR tool's profile lists before a screen (e.g. Horilla's switch to list view).

        Taken from the profile, not the recipe, and only while the confirmed address is still the profile's own."""
        known = profiles.profile_for(self.connector.profile)
        own = ((known.screens.get("datasets") or {}).get(dataset) or {}) if known else {}
        if not own.get("prepare") or own.get("url") != spec.get("url"):
            return
        for step in own["prepare"]:
            if off_site(step, self.connector.base_url):
                continue
            self.log(f"Preparing the {DATASETS.get(dataset, dataset)} screen ({known.name} profile): {step}")
            self._goto(page, self._url(step))
            self._ensure_logged_in(page, self._url(step))

    def _show_every_status(self, page, label: str, url: str) -> str:
        """A list screen that shows only some statuses by default (Horilla's contracts: Draft and Active, so
        expired contracts and the old salaries in them are hidden) is opened again with every status. Old salary
        records are what arrears are worked out from. Kept only if the screen then shows at least as many rows."""
        try:
            filters = page.evaluate(STATUS_FILTERS_JS)
        except Exception:  # noqa: BLE001 - a page without such a form is simply read as it is
            return url
        parts = urlparse(page.url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        widened = []
        for f in filters:
            values = [o["value"] for o in f["all"]]
            if not f["selected"] or set(f["selected"]) >= set(values):
                continue  # no filter set, or every status already shown
            if any(k == f["name"] for k, _ in query):
                continue  # the address already chooses statuses (e.g. from a profile)
            query += [(f["name"], v) for v in values]
            hidden = [o["text"] for o in f["all"] if o["value"] not in f["selected"]]
            widened.append(f"'{f['name']}' showed only {', '.join(f['selected'])}; also reading {', '.join(hidden)}")
        if not widened:
            return url
        before = page.evaluate(ROW_COUNT_JS)
        wider = urlunparse(parts._replace(query=urlencode(query)))
        if off_site(wider, self.connector.base_url):
            return url
        self._goto(page, wider)
        self._ensure_logged_in(page, wider)
        after = page.evaluate(ROW_COUNT_JS)
        if after < before:
            self.log(f"{label}: showing every status gave fewer rows ({after} < {before}); reading the screen as it was.", "warning")
            self._goto(page, url)
            self._ensure_logged_in(page, url)
            return url
        self.log(f"{label}: the screen hides some statuses by default ({'; '.join(widened)}). "
                 f"Reading every status so older records are included: {wider}")
        return wider

    def _pick_table(self, found: list[dict], dataset: str) -> dict | None:
        best, best_score = None, -1
        hint = {"employees": "employees", "compensation": "compensation", "leave": "leave"}.get(dataset, "")
        for t in found:
            if not t["rows"]:
                continue
            guess = heuristic_mapping([h for h in t["headers"] if h], hint)
            mapped = sum(1 for v in guess.values() if v["target"] != IGNORE)
            score = mapped * 10 + len(t["rows"])
            if score > best_score:
                best, best_score = t, score
        return best

    def read_dataset(self, page, dataset: str, spec: dict) -> dict | None:
        label = DATASETS.get(dataset, dataset)
        url = self._url(spec["url"])
        self._prepare(page, dataset, spec)
        self.log(f"Opening the {label} screen: {url}")
        if spec.get("steps"):
            self._follow_path(page, spec, label)  # raises PathBroken when a saved click is gone
        else:
            self._goto(page, url)
            self._ensure_logged_in(page, url)
            url = self._show_every_status(page, label, url)
        headers, rows, links = None, [], []
        shown, method = None, ""
        screenshots, issues = [], []
        seen_pages = set()
        complete, stopped = True, ""
        page_no = 0
        pages_shown = None  # how many pages the screen says it has, when it says
        current_url = page.url
        while True:
            page_no += 1
            if page_no > settings.AGENT_MAX_PAGES_PER_DATASET:
                complete, stopped = False, f"more than {settings.AGENT_MAX_PAGES_PER_DATASET} pages"
                break
            snap = page.evaluate(EXTRACT_JS)
            if self.is_login_page(page):
                self._ensure_logged_in(page, current_url)
                if spec.get("steps") and page_no == 1:
                    return self.read_dataset(page, dataset, spec)  # the list appears only after the clicks: replay them
                snap = page.evaluate(EXTRACT_JS)
            screenshots.append(self._screenshot(page, f"{dataset}-page-{page_no}"))
            shown = shown if shown is not None else total_shown(snap["text"])
            if snap.get("pages") and snap["pages"].get("total"):
                pages_shown = max(pages_shown or 0, int(snap["pages"]["total"]))
            table = self._pick_table(snap["tables"], dataset)
            if table is None:
                table = self._ai_read(snap, dataset)
                if table is None:
                    if page_no == 1:
                        self.log(f"No table found on the {label} screen.", "error")
                        return None
                    issues.append({"level": "error", "code": "page_unreadable", "message": f"page {page_no} had no readable table"})
                    complete, stopped = False, f"page {page_no} could not be read"
                    break
            signature = hashlib.sha256(json.dumps(table["rows"]).encode()).hexdigest()
            if signature in seen_pages:
                page_no -= 1  # not a new page
                self.log("The next page repeats the previous one; stopping.", "warning")
                break
            seen_pages.add(signature)
            if headers is None:
                headers, method = table["headers"], table["method"]
            elif table["headers"] != headers:
                issues.append({"level": "error", "code": "headers_changed", "message": f"page {page_no} has different columns than page 1"})
                complete, stopped = False, "columns changed between pages"
                break
            rows.extend(table["rows"])
            links.extend(table.get("links") or [None] * len(table["rows"]))
            self.log(f"{label} page {page_no}: {len(table['rows'])} rows ({table['method']}).")
            if not snap.get("next"):
                break
            if snap.get("pages") and snap["pages"].get("current") and snap["pages"]["current"] >= snap["pages"]["total"]:
                break  # the screen says this is its last page
            before = page.url
            if snap["next"].get("href"):
                current_url = snap["next"]["href"]
                self._goto(page, current_url)
            else:
                if not self._click_next(page):
                    issues.append({"level": "error", "code": "next_not_clickable", "message": f"the next-page control on page {page_no} could not be clicked"})
                    complete, stopped = False, f"the next-page control on page {page_no} could not be clicked"
                    self.log(f"{label}: the next-page control on page {page_no} could not be clicked; the list is incomplete.", "error")
                    break
                self._settle(page)
                self._wait_for_new_rows(page, dataset, signature)
                self.pages_visited += 1
                current_url = page.url if page.url != before else current_url
            if self._ensure_logged_in(page, current_url) and not snap["next"].get("href"):
                # click-based paging cannot be resumed by URL: start over to be safe
                self.log("Paging is click-based, so the screen is read again from page 1.", "warning")
                return self.read_dataset(page, dataset, spec)

        if pages_shown and page_no < pages_shown and complete:
            # The screen says there are more pages than could be reached: never return part of a list as if it were all.
            complete, stopped = False, f"read {page_no} of the {pages_shown} pages the screen shows; no way to the next page was found"
            issues.append({"level": "error", "code": "pages_missing", "message": f"only {page_no} of {pages_shown} pages could be read"})
            self.log(f"{label}: the screen has {pages_shown} pages but only {page_no} could be read.", "error")
        headers = self._unique_headers(headers or [])
        record_rows = []
        for r in rows:
            cells = list(r) + [""] * (len(headers) - len(r))
            record_rows.append({h: cells[i] for i, h in enumerate(headers) if h})
        # Drop columns with no heading (checkboxes, action buttons)
        headers = [h for h in headers if h]
        if shown is not None:
            if shown == len(record_rows):
                self.log(f"{label}: read {len(record_rows)} rows; the screen says {shown}. Complete.")
            else:
                self.log(f"{label}: read {len(record_rows)} rows but the screen says {shown}.", "warning")
        elif pages_shown and page_no == pages_shown:
            self.log(f"{label}: read {len(record_rows)} rows over all {page_no} pages the screen shows.")
        else:
            self.log(f"{label}: read {len(record_rows)} rows over {page_no} page(s); the screen shows no total to compare with.")
        return {
            "name": f"{label} screen",
            "dataset_hint": dataset,
            "source_url": url,
            "headers": headers,
            "rows": record_rows,
            "_links": links,
            "read_info": {"method": method, "pages": page_no, "pages_shown": pages_shown, "total_shown": shown, "complete": complete, "stopped_because": stopped,
                          "issues": issues, "screenshots": [s for s in screenshots if s], "relogins": self.relogins},
        }

    @staticmethod
    def _unique_headers(headers: list[str]) -> list[str]:
        out, seen = [], {}
        for h in headers:
            h = h.strip()
            if not h:
                out.append("")
                continue
            if h in seen:
                seen[h] += 1
                h = f"{h} ({seen[h]})"
            else:
                seen[h] = 1
            out.append(h)
        return out

    def _ai_read(self, snap: dict, dataset: str) -> dict | None:
        if not ai_client.is_model():
            return None
        self.log(f"No table structure found on {snap['url']}; asking the AI to read the visible text.", "warning")
        interaction = self._db(ai_tasks.extract_records, company=self.company, url=snap["url"], dataset=dataset,
                               page_text=snap["text"])
        response = interaction.response or {}
        if any(c.get("level") == "error" for c in interaction.validation) or not response.get("rows"):
            self.log("The AI's reading did not match the screen, so it was not used.", "error")
            return None
        return {"method": f"AI reading (interaction #{interaction.pk}, checked against the screen)", "headers": response["headers"],
                "rows": response["rows"], "links": [None] * len(response["rows"])}

    def read_details(self, page, table: dict, spec: dict | None = None, checks: list | None = None):
        """Open each row's own page (by the address saved while reading the list: never back and forth through the
        list), click the confirmed tabs on it, and add its label/value fields to the row."""
        spec, checks = spec or {}, checks if checks is not None else []
        tabs = spec.get("detail_steps") or []
        links = table.pop("_links", [])
        added = set()
        opened, tab_missing = 0, 0
        for index, (row, link) in enumerate(zip(table["rows"], links), start=1):
            if not link or urlparse(link).netloc != urlparse(self.connector.base_url).netloc:
                continue
            self._goto(page, link)
            self._ensure_logged_in(page, link)
            opened += 1
            pairs = dict(page.evaluate(FIELDS_JS))
            for tab in tabs:
                try:
                    self._click_step(page, tab)
                except PathBroken:
                    tab_missing += 1
                    continue
                if self.is_login_page(page):  # expired on the click: sign in, reopen this page, click again
                    self._ensure_logged_in(page, link)
                    self._click_step(page, tab)
                for k, v in page.evaluate(FIELDS_JS).items():
                    pairs.setdefault(k, v)
            for k, v in pairs.items():
                if k and k not in row:
                    row[k] = v
                    added.add(k)
            if index % 25 == 0:
                self.log(f"Read {index} of {len(table['rows'])} detail pages.")
        for k in sorted(added):
            if k not in table["headers"]:
                table["headers"].append(k)
        for row in table["rows"]:
            for h in table["headers"]:
                row.setdefault(h, "")
        if added:
            self.log(f"Read {opened} detail pages" + (f" (tabs: {', '.join(t['text'] for t in tabs)})" if tabs else "")
                     + f"; added fields: {', '.join(sorted(added))}.")
        if tab_missing:
            names = ", ".join(t["text"] for t in tabs)
            whole = tab_missing >= opened * len(tabs)
            self.log(f"A confirmed tab ({names}) was missing on {tab_missing} detail page visits.", "error" if whole else "warning")
            checks.append({"level": "error" if whole else "warning", "code": "detail_tab_missing",
                           "message": f"Employee pages: the confirmed tab ({names}) was not found {tab_missing} time(s) on {opened} pages. "
                                      + ("The employee page changed: find the screens again and confirm." if whole else
                                         "Some employees' fields may be missing; check them in the review.")})
