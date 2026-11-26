import React from "react";

// Shown while the agent reads an HR tool: the screen it is on, rows lifting off the page into Prosperr, and how many
// rows each list has given so far. Everything shown comes from the agent's own log (see browser_agent.py), so the
// screen, page number, row counts and detail-page progress are the real ones; only the motion is decoration.

const LISTS = ["Employees", "Salary", "Leave"];

/** What the agent is doing, worked out from its log lines. */
export function readProgress(log) {
  const lists = Object.fromEntries(LISTS.map((l) => [l, { rows: 0, state: "waiting" }]));
  const s = { signedIn: false, current: null, url: "", page: 0, detail: null, lists };
  for (const { message: m = "" } of log || []) {
    let x;
    if (/^Signed in to /.test(m)) s.signedIn = true;
    else if ((x = m.match(/^Opening the (\w+) screen: (\S+)/))) {
      if (s.current && lists[s.current]) lists[s.current].state = "done";
      s.current = x[1]; s.url = x[2]; s.page = 0; s.detail = null;
      if (lists[x[1]]) { lists[x[1]].state = "reading"; lists[x[1]].rows = 0; }
    } else if ((x = m.match(/^(\w+) page (\d+): (\d+) rows/))) {
      s.page = Number(x[2]);
      if (lists[x[1]]) lists[x[1]].rows += Number(x[3]);
    } else if ((x = m.match(/^(\w+): read (\d+) rows/))) {
      if (lists[x[1]]) lists[x[1]].rows = Number(x[2]);
    } else if ((x = m.match(/^Read (\d+) of (\d+) detail pages/))) {
      s.detail = { done: Number(x[1]), total: Number(x[2]) };
    } else if (/^Read \d+ detail pages/.test(m)) {
      s.detail = null;
    }
  }
  return s;
}

export default function AgentReader({ job }) {
  const log = job.log || [];
  const p = readProgress(log);
  const last = log.length ? log[log.length - 1].message : "Starting the browser…";
  const mode = !p.signedIn ? "login" : p.detail ? "detail" : "list";
  let url = p.url || job.source_system || "";
  try { const u = new URL(p.url); url = u.host + u.pathname; } catch { /* keep */ }

  return (
    <div className="card ar" role="status" aria-live="polite">
      <div className="ar-head">
        <h2>The agent is reading {job.source_system || "the HR tool"}</h2>
        <span className="muted small">This page updates by itself.</span>
      </div>
      <div className="ar-grid">
        {/* the HR tool's screen, as the agent sees it */}
        <div className="af-window ar-window" aria-hidden="true">
          <div className="af-bar">
            <span className="af-dots"><i /><i /><i /></span>
            <span className="af-url"><span className="af-lock">🔒</span>{mode === "login" ? `${job.source_system || "HR tool"} · sign in` : url}</span>
          </div>
          {mode === "login" && (
            <div className="ar-body ar-login"><span className="ar-spinner" /><span>Signing in…</span></div>
          )}
          {mode === "list" && (
            <div className="ar-body" key={`${p.current}-${p.page}`}>
              <div className="ar-title">{p.current || "Opening the screen"}</div>
              <div className="ar-table">
                <span className="ar-row is-head"><i /><i /><i /><i /></span>
                {Array.from({ length: 10 }, (_, i) => <span key={i} className="ar-row" style={{ "--i": i }}><i /><i /><i /><i /></span>)}
                <span className="ar-scan" />
              </div>
              <div className="ar-pager"><span>Page {p.page || 1}</span><b>Next ›</b></div>
            </div>
          )}
          {mode === "detail" && (
            <div className="ar-body ar-detail" key={p.detail.done}>
              <div className="ar-title">{p.current} · details</div>
              <div className="ar-profile">
                <span className="ar-avatar" />
                <div><i style={{ width: "60%" }} /><i style={{ width: "40%" }} /></div>
              </div>
              <div className="ar-fields">{Array.from({ length: 6 }, (_, i) => <span key={i} style={{ "--i": i }}><i /><i /></span>)}</div>
              <span className="ar-scan" />
            </div>
          )}
          <span className={`af-cursor ar-cursor ar-cursor-${mode}`}>
            <svg viewBox="0 0 20 20"><path d="M3 2 L3 16 L7 12.5 L9.6 18 L12 17 L9.5 11.6 L15 11.6 Z" /></svg>
            <span className="ar-click" />
            <span className="af-tag">Agent</span>
          </span>
        </div>

        {/* rows lifting off the screen */}
        <div className="ar-stream" aria-hidden="true">
          {mode !== "login" && Array.from({ length: 6 }, (_, i) => (
            <span key={i} className="ar-chip" style={{ "--i": i, "--y": `${18 + ((i * 37) % 64)}%` }}><i /><i /></span>
          ))}
        </div>

        {/* what has arrived so far */}
        <div className="ar-bucket">
          <div className="ar-bucket-title">Into Prosperr.io</div>
          {LISTS.map((l) => {
            const it = p.lists[l];
            const detail = p.current === l && p.detail;
            return (
              <div key={l} className={`ar-list is-${it.state}`}>
                <span className="ar-state" aria-hidden="true">{it.state === "done" ? "✓" : ""}</span>
                <span className="ar-list-name">{l}</span>
                <b className="ar-count" key={it.rows}>{it.rows ? `${it.rows} rows` : it.state === "reading" ? "reading…" : "waiting"}</b>
                {detail && (
                  <span className="ar-progress" title={`${detail.done} of ${detail.total} detail pages`}>
                    <i style={{ width: `${Math.round((detail.done / detail.total) * 100)}%` }} />
                    <small>{detail.done} of {detail.total} detail pages</small>
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <p className="ar-now" title={last}><span className="ar-spinner" aria-hidden="true" /><span>{last}</span></p>
    </div>
  );
}
