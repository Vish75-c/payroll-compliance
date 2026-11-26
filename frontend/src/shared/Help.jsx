import React, { useEffect, useState } from "react";
import { HELP, HELP_ORDER } from "./help.js";
import { Drawer } from "./ui.jsx";

// Help for a page: a "?" by its title, which also opens by itself the first time a person opens that page (remembered
// per person in this browser; in a private window it simply does not open by itself).
let helpUser = "anon";
export function setHelpUser(id) { helpUser = String(id); }

const seenKey = (topic) => `help-seen:${helpUser}:${topic}`;
function seen(topic) { try { return window.localStorage.getItem(seenKey(topic)) === "1"; } catch { return true; } }
function markSeen(topic) { try { window.localStorage.setItem(seenKey(topic), "1"); } catch { /* not remembered */ } }
function clearSeen() {
  try {
    Object.keys(window.localStorage).filter((k) => k.startsWith(`help-seen:${helpUser}:`)).forEach((k) => window.localStorage.removeItem(k));
  } catch { /* nothing remembered */ }
}

export function PageHelp({ topic }) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (HELP[topic] && !seen(topic)) setOpen(true); }, [topic]);
  if (!HELP[topic]) return null;
  const close = () => { markSeen(topic); setOpen(false); };
  return (
    <>
      <button type="button" className="help-btn" onClick={() => setOpen(true)} aria-label={`Help: ${HELP[topic].title}`} title="What is this?">?</button>
      {open && <HelpPopup topic={topic} onClose={close} />}
    </>
  );
}

function HelpPopup({ topic, onClose }) {
  const h = HELP[topic];
  return (
    <Drawer title={h.title} subtitle={h.intro} onClose={onClose}>
      <HelpBody topic={topic} />
      <div className="row" style={{ marginTop: 18 }}>
        <button className="primary" onClick={onClose}>Got it</button>
        <a href={`#/help`} onClick={onClose} className="small">All help →</a>
      </div>
    </Drawer>
  );
}

function HelpBody({ topic, onLink }) {
  const h = HELP[topic];
  return (
    <>
      <ul className="help-points">{h.points.map((p, i) => <li key={i}>{p}</li>)}</ul>
      {h.seeAlso?.length > 0 && (
        <p className="small" style={{ marginTop: 14, marginBottom: 0 }}>See also: {h.seeAlso.filter((t) => HELP[t]).map((t, i) => (
          <React.Fragment key={t}>{i ? " · " : ""}<a href={`#/help`} onClick={(e) => { if (onLink) { e.preventDefault(); onLink(t); } }}>{HELP[t].title}</a></React.Fragment>
        ))}</p>
      )}
    </>
  );
}

export default function HelpPage() {
  const [query, setQuery] = useState("");
  const [cleared, setCleared] = useState(false);
  const q = query.trim().toLowerCase();
  const topics = HELP_ORDER.filter((t) => !q || [HELP[t].title, HELP[t].intro, ...HELP[t].points].join(" ").toLowerCase().includes(q));
  const jump = (t) => document.getElementById(`help-${t.replace(".", "-")}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <div className="help-page">
      <div className="topbar">
        <div><h1>Help</h1></div>
        <button onClick={() => { clearSeen(); setCleared(true); }} disabled={cleared}>{cleared ? "Tips will show again" : "Show page tips again"}</button>
      </div>
      <input className="help-search" placeholder="Search help" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search help" />
      {!q && (
        <nav className="help-index" aria-label="Help topics">
          {HELP_ORDER.map((t) => <button key={t} type="button" className="ghost small" onClick={() => jump(t)}>{HELP[t].title}</button>)}
        </nav>
      )}
      {topics.length === 0 && <p className="muted">Nothing matches “{query}”.</p>}
      {topics.map((t) => (
        <section key={t} id={`help-${t.replace(".", "-")}`} className="card help-topic">
          <h2>{HELP[t].title}</h2>
          <p style={{ marginTop: 0 }}>{HELP[t].intro}</p>
          <HelpBody topic={t} onLink={jump} />
        </section>
      ))}
    </div>
  );
}
