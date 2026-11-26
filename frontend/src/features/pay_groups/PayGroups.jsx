import React, { useState } from "react";
import { api } from "../../shared/api.js";
import { Banner, Field, Loading, matches, monthLabel, useAction, useLoad, when } from "../../shared/ui.jsx";
import { PAY_CYCLES, PayCycleFields, cycleExample } from "../rules/RuleForms.jsx";
import { PageHelp } from "../../shared/Help.jsx";

// Pay groups: people paid on the same cycle and the same day. Each person's group comes from the HR data (a "Pay group"
// column, or a group's department / state / designation rule); the finance team can set it by hand for exceptions.

const SOURCES = { manual: "set by hand", hr_data: "HR data", rule: "rule", default: "default" };
const COVERS = [["departments", "Departments"], ["work_states", "Work states"], ["designations", "Designations"]];
const thisMonth = () => new Date().toISOString().slice(0, 7);
const modeLabel = (mode) => (PAY_CYCLES.find(([k]) => k === mode) || [mode, mode])[1];

export function cycleText(c, companyPayDay) {
  if (!c) return "";
  const pay = c.pay_day || companyPayDay;
  return `${modeLabel(c.mode)}${c.mode !== "calendar" ? ` · ${cycleExample(c)}` : ""}${pay ? ` · paid on the ${ordinal(pay)}` : ""}`;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function Chips({ values, onChange, suggestions, listId, placeholder }) {
  const [text, setText] = useState("");
  const add = () => { const v = text.trim(); if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...values, v]); setText(""); };
  return (
    <div className="chips-input">
      {values.map((v) => (
        <span key={v} className="chip">{v} <button type="button" className="chip-x" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button></span>
      ))}
      <input list={listId} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} onBlur={add} />
      <datalist id={listId}>{(suggestions || []).map((s) => <option key={s} value={s} />)}</datalist>
    </div>
  );
}

function GroupForm({ group, suggestions, firstOpen, onSaved, onCancel, companyId }) {
  const now = group?.now;
  const isDefault = group?.default;
  const [f, setF] = useState({
    name: group?.name || "", mode: now?.mode || "calendar", start_day: now?.start_day || 1, pay_day: now?.pay_day || "",
    covers: { departments: [], work_states: [], designations: [], ...(now?.covers || {}) }, from: firstOpen || "", note: "",
  });
  const [busy, act] = useAction();
  const cycleOk = f.mode === "calendar" || (Number(f.start_day) >= 2 && Number(f.start_day) <= 28);
  const save = async () => {
    const body = { mode: f.mode, start_day: f.mode === "calendar" ? 1 : Number(f.start_day), pay_day: f.pay_day, covers: f.covers,
      from: f.from, note: f.note };
    const ok = await act("save", () => (group ? api.post(`/pay-groups/${group.id}/cycles`, body)
      : api.post(`/companies/${companyId}/pay-groups`, { ...body, name: f.name })), group ? "Pay group changed" : "Pay group added");
    if (ok) onSaved();
  };
  return (
    <div className="card pay-group-form">
      <h3 style={{ marginTop: 0 }}>{group ? `Change ${group.name}` : "New pay group"}</h3>
      <div className="grid3">
        {!group && <Field label="Name"><input value={f.name} placeholder="e.g. Factory staff" onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>}
        <PayCycleFields v={{ mode: f.mode, start_day: f.start_day }} onChange={(c) => setF({ ...f, ...c })} />
        {!isDefault && <Field label="Paid on (day)" hint="The first such day after the cycle ends. Sets when its TDS is due.">
          <input type="number" min="1" max="31" value={f.pay_day} onChange={(e) => setF({ ...f, pay_day: e.target.value })} />
        </Field>}
        <Field label="From month" hint={firstOpen ? `${monthLabel(firstOpen)} or later: earlier months are final` : "Empty: from the first month"}>
          <input type="month" min={firstOpen || undefined} value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
        </Field>
      </div>
      {!isDefault && (
        <div style={{ marginTop: 8 }}>
          <div className="group-label">Who it covers</div>
          <div className="grid3">
            {COVERS.map(([key, label]) => (
              <Field key={key} label={label}>
                <Chips values={f.covers[key] || []} onChange={(v) => setF({ ...f, covers: { ...f.covers, [key]: v } })}
                  suggestions={suggestions?.[key]} listId={`pg-${key}`} placeholder="Type and press Enter" />
              </Field>
            ))}
          </div>
        </div>
      )}
      <Field label="Note"><input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="Why, e.g. plant moves to the 26th-to-25th cycle" /></Field>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="primary" disabled={!!busy || !cycleOk || (!group && !f.name.trim())} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        <button className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export function PayGroupsCard({ company, me }) {
  const { data, error, reload } = useLoad(() => api.get(`/companies/${company.id}/pay-groups`), [company.id]);
  const [editing, setEditing] = useState(null); // a group, or "new"
  const [open, setOpen] = useState(null);
  if (!data) return <Loading error={error} />;
  const isMaker = me.roles.includes("maker");
  const saved = () => { setEditing(null); reload(true); };
  return (
    <div className="card pay-groups">
      <div className="card-head">
        <div>
          <div className="title-row"><h2>Pay groups</h2><PageHelp topic="pay_groups" /></div>
          <p className="muted small" style={{ margin: 0 }}>{monthLabel(data.period)} · <a href="#/employees">Employees</a></p>
        </div>
        {isMaker && <button className="small" onClick={() => setEditing("new")}>+ New pay group</button>}
      </div>
      {editing === "new" && <GroupForm suggestions={data.suggestions} firstOpen={data.first_open} companyId={company.id} onSaved={saved} onCancel={() => setEditing(null)} />}
      {data.groups.map((g) => (
        <div key={g.id} className="version current">
          <div className="row between">
            <div>
              <b>{g.name}</b> {g.default && <span className="chip">default</span>} <span className="chip info">{g.people} {g.people === 1 ? "person" : "people"}</span>
              <div className="small">{cycleText(g.now, g.now?.company_pay_day)}</div>
              {g.dates && <div className="muted small">{monthLabel(data.period)}: salary for {g.dates.salary}{g.dates.attendance !== g.dates.salary ? `, unpaid days counted ${g.dates.attendance}` : ""}</div>}
              {!g.default && g.now && <div className="muted small">Covers: {COVERS.map(([k, l]) => (g.now.covers?.[k] || []).length ? `${l.toLowerCase()} ${g.now.covers[k].join(", ")}` : null).filter(Boolean).join("; ") || "only people put in it by the HR data column or by hand"}</div>}
            </div>
            <div className="row">
              <button className="ghost small" onClick={() => setOpen(open === g.id ? null : g.id)}>{open === g.id ? "Hide history" : `History (${g.cycles.length})`}</button>
              {isMaker && <button className="small" onClick={() => setEditing(g)}>Change from a month</button>}
            </div>
          </div>
          {editing?.id === g.id && <GroupForm group={g} suggestions={data.suggestions} firstOpen={data.first_open} companyId={company.id} onSaved={saved} onCancel={() => setEditing(null)} />}
          {open === g.id && (
            <ul className="notes-list" style={{ marginTop: 6 }}>
              {[...g.cycles].reverse().map((c) => (
                <li key={c.id} className="small"><b>{c.from ? `From ${monthLabel(c.from)}` : "From the start"}</b>: {cycleText(c, c.company_pay_day)}
                  {c.note ? ` — ${c.note}` : ""}{c.created_by ? ` · ${c.created_by.name}, ${when(c.created_at)}` : ""}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Employees page

export default function EmployeesPage({ company, me }) {
  const [period, setPeriod] = useState("");
  const { data, error, reload } = useLoad(() => api.get(`/companies/${company.id}/pay-group-employees${period ? `?period=${period}` : ""}`), [company.id, period]);
  const groups = useLoad(() => api.get(`/companies/${company.id}/pay-groups`), [company.id]);
  const [query, setQuery] = useState("");
  const [only, setOnly] = useState("");
  const [picked, setPicked] = useState(new Set());
  const [open, setOpen] = useState(null);
  const [move, setMove] = useState({ group: "", from: "", note: "" });
  const [busy, act] = useAction();
  if (!data || !groups.data) return <Loading error={error || groups.error} />;
  const isMaker = me.roles.includes("maker");
  const shown = data.employees.filter((e) => (!only || e.group === only) && matches(query, e.name, e.code, e.department));
  const toggle = (code) => { const next = new Set(picked); next.has(code) ? next.delete(code) : next.add(code); setPicked(next); };
  const allShown = shown.length > 0 && shown.every((e) => picked.has(e.code));
  const firstOpen = data.first_open;
  const send = async (clear) => {
    const ok = await act(clear ? "clear" : "move", () => api.post(`/companies/${company.id}/pay-group-assignments`, {
      codes: [...picked], group: move.group, from: move.from, note: move.note, clear }),
    clear ? "Back to what the HR data says" : "Moved");
    if (ok) { setPicked(new Set()); reload(true); groups.reload(true); }
  };
  return (
    <div>
      <div className="topbar">
        <div>
          <div className="title-row"><h1>Employees</h1><PageHelp topic="employees" /></div>
        </div>
        <Field label="Month"><input type="month" value={period || data.period} onChange={(e) => setPeriod(e.target.value)} /></Field>
      </div>
      <div className="row" style={{ marginBottom: 10, flexWrap: "wrap" }}>
        <input style={{ minWidth: 240 }} placeholder="Search by name, ID or department" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select value={only} onChange={(e) => setOnly(e.target.value)} aria-label="Pay group">
          <option value="">All pay groups ({data.employees.length})</option>
          {groups.data.groups.map((g) => <option key={g.code} value={g.code}>{g.name} ({data.employees.filter((e) => e.group === g.code).length})</option>)}
        </select>
      </div>
      {isMaker && picked.size > 0 && (
        <div className="card bulk-bar">
          <b>{picked.size} selected</b>
          <select value={move.group} onChange={(e) => setMove({ ...move, group: e.target.value })} aria-label="Move to">
            <option value="">Move to…</option>
            {groups.data.groups.map((g) => <option key={g.code} value={g.code}>{g.name}</option>)}
          </select>
          <input type="month" min={firstOpen || undefined} value={move.from} onChange={(e) => setMove({ ...move, from: e.target.value })} aria-label="From month" />
          <input style={{ flex: 1, minWidth: 180 }} placeholder="Note (why)" value={move.note} onChange={(e) => setMove({ ...move, note: e.target.value })} />
          <button className="primary" disabled={!!busy || !move.group || !move.from} onClick={() => send(false)}>Move</button>
          <button disabled={!!busy || !move.from} onClick={() => send(true)} title="Go back to the group the HR data gives">Clear hand setting</button>
          <button className="ghost" onClick={() => setPicked(new Set())}>Cancel</button>
          {firstOpen && <span className="muted small">From {monthLabel(firstOpen)} or later.</span>}
        </div>
      )}
      {data.employees.length === 0 ? <Banner kind="info">No reviewed HR data yet.</Banner> : (
        <div className="table-wrap card" style={{ padding: 0 }}>
          <table>
            <thead><tr>
              {isMaker && <th style={{ width: 32 }}><input type="checkbox" aria-label="Select all shown" checked={allShown} onChange={() => setPicked(allShown ? new Set() : new Set(shown.map((e) => e.code)))} /></th>}
              <th>Employee</th><th>Department</th><th>State</th><th>Pay group</th><th>Why</th><th>Dates in {monthLabel(data.period)}</th><th />
            </tr></thead>
            <tbody>{shown.map((e) => (
              <React.Fragment key={e.code}>
                <tr>
                  {isMaker && <td><input type="checkbox" aria-label={`Select ${e.name}`} checked={picked.has(e.code)} onChange={() => toggle(e.code)} /></td>}
                  <td><b>{e.name}</b><div className="muted small">{e.code}{e.designation ? ` · ${e.designation}` : ""}</div></td>
                  <td>{e.department || "—"}</td>
                  <td>{e.work_state || "—"}</td>
                  <td><b>{e.group_name}</b>{e.upcoming.length > 0 && <div className="muted small">→ {e.upcoming[e.upcoming.length - 1].group_name} from {monthLabel(e.upcoming[e.upcoming.length - 1].from)}</div>}</td>
                  <td><span className={`chip ${e.source === "manual" ? "warn" : e.source === "default" ? "" : "info"}`}>{SOURCES[e.source] || e.source}</span>
                    {e.reason && e.source !== "manual" && <div className="muted small">{e.reason}</div>}</td>
                  <td className="small">
                    {e.salary}{e.salary_days !== e.basis_days && ` (${e.salary_days} of ${e.basis_days} days)`}
                    {e.transition && <span className="chip warn" style={{ marginLeft: 6 }} title="The pay cycle changed: these dates bridge the last paid month and the new cycle, so no day is paid twice or missed">transition</span>}
                    {e.attendance && <div className="muted small">Unpaid days counted {e.attendance}</div>}
                  </td>
                  <td><button className="ghost small" onClick={() => setOpen(open === e.code ? null : e.code)}>{open === e.code ? "Hide" : "History"}</button></td>
                </tr>
                {open === e.code && (
                  <tr><td colSpan={isMaker ? 8 : 7}>
                    {e.history.length === 0 ? <span className="muted small">Always in {e.group_name}.</span> : (
                      <ul className="notes-list">{[...e.history].reverse().map((h) => (
                        <li key={h.id} className="small"><b>{h.from ? `From ${monthLabel(h.from)}` : "From the start"}</b>: {h.group_name} ({SOURCES[h.source]}{h.reason && h.source !== "manual" ? `, ${h.reason}` : ""})
                          {h.note ? ` — ${h.note}` : ""}{h.by ? ` · ${h.by.name}, ${when(h.at)}` : ""}</li>
                      ))}</ul>
                    )}
                  </td></tr>
                )}
              </React.Fragment>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
