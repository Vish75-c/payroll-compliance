import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api.js";
import { go } from "../../shared/routing.js";
import {
  AIChip, Banner, Checks, Field, Loading, Segmented, Select, StatusChip, TableToolbar, Tabs, day, inr, matches, monthLabel, stateName,
  useAction, useLoad, usePoll, when,
} from "../../shared/ui.jsx";
import { PageHelp } from "../../shared/Help.jsx";
import AgentFinder from "./AgentFinder.jsx";
import AgentReader from "./AgentReader.jsx";

const LOW_CONFIDENCE = 0.7;

const DATASET_LABELS = { employees: "Employees", compensation: "Salary", leave: "Leave", attendance: "Attendance" };
// Screens the browser agent reads in an HR tool. Attendance comes only as an uploaded file (an export or punch report).
const SCREEN_KEYS = ["employees", "compensation", "leave"];

// =================================================================== sources
export function DataSources({ company, me }) {
  const conns = useLoad(() => api.get(`/companies/${company.id}/connectors`), [company.id]);
  const jobs = useLoad(() => api.get(`/companies/${company.id}/jobs`), [company.id]);
  const snaps = useLoad(() => api.get(`/companies/${company.id}/snapshots`), [company.id]);
  const isMaker = me.roles.includes("maker");
  // One HR tool at a time, picked from a dropdown. Horilla is shown first; a tool just connected is picked for you.
  const [toolId, setToolId] = useState(null);
  const [pickNewest, setPickNewest] = useState(false);
  const list = conns.data || [];
  useEffect(() => {
    if (!list.length) return;
    if (pickNewest) { setToolId(Math.max(...list.map((c) => c.id))); setPickNewest(false); return; }
    if (!list.some((c) => c.id === toolId)) setToolId((list.find((c) => /horilla/i.test(`${c.name} ${c.profile || ""}`)) || list[0]).id);
  }, [conns.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const tool = list.find((c) => c.id === toolId);

  return (
    <div>
      <div className="topbar">
        <div>
          <div className="title-row"><h1>HR data</h1><PageHelp topic="hr_data" /></div>
        </div>
      </div>
      {!isMaker && <Banner kind="info">Read only: HR data is read and uploaded by the finance team.</Banner>}
      <div className="grid2">
        <div className="card">
          <div className="row between" style={{ marginBottom: 10 }}>
            <h2 style={{ margin: 0 }}>Read from an HR tool</h2>
            {list.length > 1 && (
              <select aria-label="HR tool" value={toolId || ""} onChange={(e) => setToolId(Number(e.target.value))} style={{ width: "auto", minWidth: 180 }}>
                {list.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            )}
          </div>
          {!conns.data ? <Loading error={conns.error} /> : tool && <Connector key={tool.id} conn={tool} isMaker={isMaker} onChange={conns.reload} />}
          {isMaker && <AddConnector companyId={company.id} onAdded={() => { setPickNewest(true); conns.reload(); }} />}
        </div>
        <div className="card">
          <h2>Upload spreadsheets</h2>
          <UploadFiles disabled={!isMaker} onUploaded={(job) => go(`/data/job/${job.id}`)}
            send={(fd) => api.upload(`/companies/${company.id}/uploads`, fd)} />
        </div>
      </div>

      <div className="grid2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Recent reads</h2>
          {!jobs.data ? <Loading error={jobs.error} /> : jobs.data.length === 0 ? <div className="empty">Nothing read yet.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Source</th><th>When</th><th>Status</th><th></th></tr></thead>
              <tbody>{jobs.data.map((j) => (
                <tr key={j.id}>
                  <td>{j.id}</td><td>{j.source_system}{j.original_filename && <div className="muted small">{j.original_filename}</div>}</td>
                  <td className="small">{when(j.created_at)}</td>
                  <td><StatusChip status={j.status} label={j.status_label} /></td>
                  <td><button className="small" onClick={() => go(`/data/job/${j.id}`)}>Open</button></td>
                </tr>))}
              </tbody></table></div>
          )}
        </div>
        <div className="card">
          <h2>Data snapshots</h2>
          {!snaps.data ? <Loading error={snaps.error} /> : snaps.data.length === 0 ? <div className="empty">None yet.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Source</th><th>Employees</th><th>Checks</th><th>Status</th><th></th></tr></thead>
              <tbody>{snaps.data.map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td><td>{s.source_system}<div className="muted small">{when(s.created_at)}</div></td>
                  <td>{s.employee_count}</td>
                  <td>{s.errors ? <span className="chip bad">{s.errors} errors</span> : s.warnings ? <span className="chip warn">{s.warnings} warnings</span> : <span className="chip ok">OK</span>}</td>
                  <td><StatusChip status={s.status === "approved" ? "approved_snapshot" : s.status} label={s.status_label} /></td>
                  <td><button className="small" onClick={() => go(`/data/snapshot/${s.id}`)}>Open</button></td>
                </tr>))}
              </tbody></table></div>
          )}
        </div>
      </div>
    </div>
  );
}

const UPLOAD_SLOTS = [
  ["employees", "Employees", "ID, name, joining date"],
  ["compensation", "Salary", "amounts and start dates"],
  ["leave", "Leave", "leave requests"],
  ["attendance", "Attendance", "daily check-ins · optional"],
  ["file", "Everything in one file", "one workbook, a sheet per list"],
];

const fileSize = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// Drop tiles: click or drag a file onto the kind of data it holds. Empty tiles are fine.
export function UploadFiles({ send, onUploaded, disabled, compact }) {
  const [files, setFiles] = useState({});
  const [over, setOver] = useState("");
  const [busy, act] = useAction();
  const chosen = Object.entries(files).filter(([, f]) => f);
  const locked = disabled || !!busy;
  const upload = async () => {
    const fd = new FormData();
    for (const [field, f] of chosen) fd.append(field, f);
    const job = await act("upload", () => send(fd), chosen.length > 1 ? "Files read" : "File read");
    if (job) { setFiles({}); onUploaded(job); }
  };
  const set = (field, file) => setFiles((prev) => ({ ...prev, [field]: file || null }));
  const drop = (field) => (e) => {
    e.preventDefault();
    setOver("");
    if (!locked && e.dataTransfer.files[0]) set(field, e.dataTransfer.files[0]);
  };
  return (
    <div className="stack">
      <div className={`drop-grid${compact ? " compact" : ""}`}>
        {UPLOAD_SLOTS.map(([field, label, hint]) => {
          const file = files[field];
          return (
            <div key={field} className={`drop-tile${file ? " has-file" : ""}${over === field ? " is-over" : ""}${field === "file" ? " is-all" : ""}`}
              onDragOver={(e) => { e.preventDefault(); if (!locked) setOver(field); }} onDragLeave={() => setOver("")} onDrop={drop(field)}>
              <label>
                <input type="file" accept=".xlsx,.xlsm,.csv" disabled={locked} onChange={(e) => { set(field, e.target.files[0]); e.target.value = ""; }} />
                <span className="drop-icon" aria-hidden="true">{file ? "✓" : "⇪"}</span>
                <span className="drop-text">
                  <b>{label}</b>
                  {file ? <span className="drop-file">{file.name} · {fileSize(file.size)}</span> : <span>{hint}</span>}
                </span>
              </label>
              {file && <button type="button" className="ghost small drop-remove" disabled={locked} onClick={() => set(field, null)} aria-label={`Remove ${file.name}`} title="Remove">×</button>}
            </div>
          );
        })}
      </div>
      <div className="row">
        <button className="primary" disabled={locked || chosen.length === 0} onClick={upload}>
          {busy ? "Reading…" : chosen.length > 1 ? `Read ${chosen.length} files` : chosen.length ? "Read file" : "Choose a file to read"}
        </button>
        <span className="muted small">.xlsx or .csv</span>
      </div>
    </div>
  );
}

export function AddConnector({ companyId, onAdded }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", base_url: "http://", username: "", password: "", profile: "" });
  const [busy, act] = useAction();
  const known = useLoad(() => (open ? api.get("/hr-tool-profiles") : Promise.resolve([])), [open]);
  if (!open) return <button className="ghost" onClick={() => setOpen(true)}>+ Connect another HR tool</button>;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const pickTool = (e) => {
    const p = (known.data || []).find((x) => x.system === e.target.value);
    setForm({ ...form, profile: e.target.value, name: form.name || (p ? p.name : "") });
  };
  const save = async () => {
    const ok = await act("add", () => api.post(`/companies/${companyId}/connectors`, form), "HR tool added");
    if (ok) { setOpen(false); onAdded(); }
  };
  return (
    <div className="card tight stack" style={{ marginTop: 10 }}>
      <div className="grid2">
        <Field label="Which HR tool?">
          <select value={form.profile} onChange={pickTool}>
            <option value="">Other / not listed</option>
            {(known.data || []).map((p) => <option key={p.system} value={p.system}>{p.name} (known: screens and exports mapped)</option>)}
          </select>
        </Field>
        <Field label="Name (e.g. Keka, greytHR, Horilla)"><input value={form.name} onChange={set("name")} /></Field>
        <Field label="Web address"><input value={form.base_url} onChange={set("base_url")} /></Field>
        <Field label="Username"><input value={form.username} onChange={set("username")} /></Field>
        <Field label="Password"><input type="password" value={form.password} onChange={set("password")} /></Field>
      </div>
      <div className="row"><button className="primary" disabled={!!busy} onClick={save}>Add</button><button onClick={() => setOpen(false)}>Cancel</button></div>
    </div>
  );
}

export function Connector({ conn, isMaker, onChange }) {
  const [busy, act] = useAction();
  const [screens, setScreens] = useState(conn.recipe?.datasets || {});
  const [editing, setEditing] = useState(false);
  const [creds, setCreds] = useState({ base_url: conn.base_url, username: conn.username, password: "" });
  useEffect(() => setScreens(conn.recipe?.datasets || {}), [conn]);
  const discover = async () => {
    const out = await act("discover", () => api.post(`/connectors/${conn.id}/discover`), "Screens found. Check them and confirm.");
    if (out) onChange();
  };
  const confirm = async () => {
    const out = await act("confirm", () => api.post(`/connectors/${conn.id}/confirm-screens`, { datasets: screens }), "Screens confirmed");
    if (out) onChange();
  };
  const read = async () => {
    const job = await act("read", () => api.post(`/connectors/${conn.id}/read`), "The agent is reading the HR tool");
    if (job) go(`/data/job/${job.id}`);
  };
  const saveCreds = async () => {
    const out = await act("save", () => api.patch(`/connectors/${conn.id}`, creds), "Saved");
    if (out) { setEditing(false); onChange(); }
  };
  const confirmed = !!conn.recipe_confirmed_at;
  const hasScreens = Object.values(conn.recipe?.datasets || {}).some((s) => s && s.url);
  return (
    <div className="card tight" style={{ marginBottom: 10 }}>
      <div className="row between">
        <div>
          <b>{conn.name}</b>
        </div>
        {isMaker && <button className="ghost small" onClick={() => setEditing(!editing)}>{editing ? "Close" : "Edit login"}</button>}
      </div>
      {editing && (
        <div className="row" style={{ marginTop: 8 }}>
          <input placeholder="Web address" value={creds.base_url} onChange={(e) => setCreds({ ...creds, base_url: e.target.value })} />
          <input placeholder="Username" value={creds.username} onChange={(e) => setCreds({ ...creds, username: e.target.value })} />
          <input placeholder="New password" type="password" value={creds.password} onChange={(e) => setCreds({ ...creds, password: e.target.value })} />
          <button className="small" disabled={!!busy} onClick={saveCreds}>Save</button>
        </div>
      )}
      <div className="group-label">Step 1 · Screens to read {conn.recipe_ai && <AIChip provider={conn.recipe_ai.provider} />}</div>
      {busy === "discover" ? <AgentFinder baseUrl={conn.base_url} username={conn.username} /> : hasScreens ? (
        <div className="stack">
          {SCREEN_KEYS.map((k) => (
            <div key={k} className="row">
              <span style={{ width: 80 }} className="small"><b>{DATASET_LABELS[k]}</b></span>
              <input style={{ flex: 1, minWidth: 200 }} value={screens[k]?.url || ""} disabled={!isMaker}
                onChange={(e) => setScreens({ ...screens, [k]: { ...(screens[k] || {}), url: e.target.value } })} />
              <ScreenPath spec={screens[k]} />
            </div>
          ))}
          {confirmed && <div className="small"><span className="chip ok">Confirmed</span> by {conn.recipe_confirmed_by?.name} on {when(conn.recipe_confirmed_at)}. Later reads go straight to these screens.</div>}
        </div>
      ) : <p className="muted small">Screens not set up yet.</p>}
      {isMaker && (
        <div className="row" style={{ marginTop: 10 }}>
          <button disabled={!!busy} onClick={discover}>{busy === "discover" ? "Looking at the HR tool…" : hasScreens ? "Find screens again" : "Find screens"}</button>
          {hasScreens && <button disabled={!!busy || JSON.stringify(screens) === JSON.stringify(conn.recipe?.datasets) && confirmed} onClick={confirm}>Confirm screens</button>}
          <span className="spacer" />
          <button className="primary" disabled={!!busy || !confirmed} onClick={read}>Step 2 · Read data now</button>
        </div>
      )}
    </div>
  );
}

// How the agent reaches a screen: the clicks it learned (each with a screenshot of the screen it was made on), the
// clicks every read repeats. Shown so a person can check the path.
function ScreenPath({ spec }) {
  if (!spec) return null;
  const learned = spec.learned_path || [];
  const steps = spec.steps || [];
  if (!learned.length && !steps.length) return null;
  return (
    <div className="muted small" style={{ flexBasis: "100%", marginLeft: 88 }}>
      {learned.length > 0 && (
        <div>Found by clicking{" "}
          {learned.map((s, i) => (
            <span key={i}>{i > 0 && " → "}
              {s.screenshot
                ? <a href={`/api/media/${s.screenshot}`} target="_blank" rel="noreferrer" title={s.reason}>{s.text}</a>
                : <b title={s.reason}>{s.text}</b>}
            </span>
          ))}
        </div>
      )}
      <div>{steps.length
        ? <>Each read opens {spec.url}, then clicks {steps.map((s) => `${s.text} (${s.role})`).join(" → ")}.</>
        : learned.length ? <>Each read goes straight to {spec.url}.</> : null}</div>
    </div>
  );
}

// =================================================================== job
export function JobPage({ jobId, me }) {
  const { data: job, error, reload } = useLoad(() => api.get(`/jobs/${jobId}`), [jobId]);
  const [busy, act] = useAction();
  const running = job && ["queued", "running"].includes(job.status);
  const poll = useCallback(() => reload(true), [reload]);
  usePoll(poll, running, 1000);
  if (!job) return <Loading error={error} />;
  const isMaker = me.roles.includes("maker");
  const unconfirmed = (job.tables || []).filter((t) => !t.mapping?.confirmed);
  // No mapping yet, or a confirmed screen that gained columns the model has not looked at yet.
  const needsSuggestion = unconfirmed.some((t) => !t.mapping || (t.mapping.options?.ask_model_for || []).length > 0);
  const suggest = async (force) => {
    const out = await act("suggest", () => api.post(`/jobs/${job.id}/suggest-mapping`, { force }), "Mapping suggested. Check each column, then confirm.");
    if (out) reload(true);
  };
  const build = async () => {
    const snap = await act("build", () => api.post(`/jobs/${job.id}/build-snapshot`), "Data snapshot created. Review it.");
    if (snap) go(`/data/snapshot/${snap.id}`);
  };
  return (
    <div>
      <div className="topbar">
        <div>
          <a href="#/data" className="small">← HR data</a>
          <div className="title-row"><h1>Read #{job.id}: {job.source_system}</h1><PageHelp topic="hr_job" /></div>
          <div className="row"><StatusChip status={job.status} label={job.status_label} />{job.original_filename && <span className="muted">{job.original_filename}</span>}<span className="muted small">{when(job.created_at)}</span></div>
        </div>
        <div className="row">
          {job.snapshot_id && <button onClick={() => go(`/data/snapshot/${job.snapshot_id}`)}>Open data snapshot #{job.snapshot_id}</button>}
          {isMaker && job.status === "ready" && !job.snapshot_id && <button className="primary" disabled={!!busy} onClick={build}>Build data snapshot</button>}
          {isMaker && job.status === "needs_mapping" && needsSuggestion && (
            <button className="primary" disabled={!!busy} onClick={() => suggest(false)}>{busy === "suggest" ? "Thinking…" : "Suggest mapping"}</button>
          )}
        </div>
      </div>
      {job.error && <Banner kind="bad" title="The read failed.">{job.error}</Banner>}
      {running && (job.kind === "browser"
        ? <AgentReader job={job} />
        : <Banner kind="info">Reading…</Banner>)}
      {(job.stats?.checks || []).length > 0 && <div className="card"><h2>Reading problems</h2><Checks checks={job.stats.checks} /></div>}
      {job.kind === "spreadsheet" && (
        <div className="card">
          <h2>Files in this read</h2>
          <ul className="small">
            {(job.stats?.files || []).map((f, i) => (
              <li key={i}><b>{f.name}</b> <span className="muted">· uploaded as {DATASET_LABELS[f.dataset] || "anything"} · {f.tables.join(", ")}</span></li>
            ))}
          </ul>
          {isMaker && !job.snapshot_id && job.status !== "done" && (
            <>
              <div className="group-label">Add another file</div>
              <UploadFiles compact send={(fd) => api.upload(`/jobs/${job.id}/files`, fd)} onUploaded={() => reload(true)} />
            </>
          )}
        </div>
      )}
      <div className="card">
        <h2>What the agent did</h2>
        <div className="log">
          {(job.log || []).map((l, i) => <div key={i} className={l.level}>{new Date(l.at).toLocaleTimeString("en-IN")}  {l.message}</div>)}
          {!job.log?.length && <div>Waiting to start…</div>}
        </div>
        {job.stats?.pages !== undefined && <p className="muted small" style={{ marginTop: 6 }}>{job.stats.pages} pages visited · {job.stats.relogins} new logins after the session expired</p>}
      </div>
      {(job.tables || []).map((t) => <TableMapping key={t.id} table={t} catalogue={job.catalogue} isMaker={isMaker} onSaved={() => reload(true)} />)}
      {isMaker && job.status === "ready" && !job.snapshot_id && (
        <div className="row" style={{ marginTop: 16 }}><span className="spacer" /><button className="primary" disabled={!!busy} onClick={build}>Build data snapshot</button></div>
      )}
      {isMaker && !needsSuggestion && unconfirmed.length > 0 && (
        <p style={{ marginTop: 8 }}><button className="ghost small" onClick={() => suggest(true)}>Suggest again</button></p>
      )}
    </div>
  );
}

function TableMapping({ table, catalogue, isMaker, onSaved }) {
  const m = table.mapping;
  const [mapping, setMapping] = useState(m?.mapping || {});
  const [valueMaps, setValueMaps] = useState(m?.value_maps || {});
  const [dateOrder] = useState(m?.options?.date_order || "DMY");
  const [busy, act] = useAction();
  const [showRows, setShowRows] = useState(false);
  useEffect(() => { setMapping(m?.mapping || {}); setValueMaps(m?.value_maps || {}); }, [m?.id]);
  const groups = useMemo(() => {
    const out = {};
    for (const t of catalogue || []) (out[t.group_label] = out[t.group_label] || []).push(t);
    return out;
  }, [catalogue]);
  const refreshValueMaps = async (next) => {
    const vm = await api.post(`/tables/${table.id}/value-maps`, { mapping: next }).catch(() => null);
    if (vm) setValueMaps((old) => {
      const merged = {};
      for (const [k, vals] of Object.entries(vm)) merged[k] = Object.fromEntries(Object.entries(vals).map(([v, g]) => [v, old?.[k]?.[v] ?? g]));
      return merged;
    });
  };
  const setTarget = (h, target) => {
    const next = { ...mapping, [h]: target };
    setMapping(next);
    const mapped = ["leave.type", "leave.status", "leave.start_breakdown", "leave.end_breakdown"];
    if (mapped.includes(target) || mapped.includes(mapping[h])) refreshValueMaps(next);
  };
  const confirm = async () => {
    const out = await act("confirm", () => api.post(`/tables/${table.id}/confirm-mapping`, { mapping, value_maps: valueMaps, options: { date_order: dateOrder } }), `Mapping for ${table.name} confirmed`);
    if (out) onSaved();
  };
  const info = table.read_info || {};
  const sample = (h) => table.rows.slice(0, 3).map((r) => r[h]).filter(Boolean).join(" · ");
  // Salary columns seen on an employee list are shown with the salary data (see SalaryFromEmployees).
  const movedToSalary = table.salary_columns || [];
  const columns = table.headers.filter((h) => !movedToSalary.includes(h));
  const suggestion = m?.suggestion || {};
  // A guess the AI was not sure of (under 70%) and that is still selected is marked for the finance team employee to check.
  const unsure = table.headers.filter((h) => !m?.confirmed && suggestion[h] && suggestion[h].target !== "ignore"
    && mapping[h] === suggestion[h].target && (suggestion[h].confidence || 0) < LOW_CONFIDENCE);
  const labelOf = (key) => Object.values(groups).flat().find((t) => t.key === key)?.label || key;
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h2>{table.name}</h2>
          <div className="muted small">
            {table.row_count} rows{info.pages ? ` from ${info.pages} page(s)` : ""}{info.method ? ` · read as ${info.method}` : ""}
            {info.total_shown != null && <> · screen says {info.total_shown} {info.total_shown === table.row_count ? <span className="chip ok">complete</span> : <span className="chip bad">mismatch</span>}</>}
            {table.source_url && <> · <a href={table.source_url} target="_blank" rel="noreferrer">{table.source_url}</a></>}
          </div>
        </div>
        <div className="row">
          {m?.confirmed ? <span className="chip ok">Mapping confirmed{m.reused ? " earlier (reused, no AI)" : ""}</span> : m ? <span className="chip warn">Suggested — confirm it</span> : <span className="chip">No mapping yet</span>}
          {m?.suggested_by && <AIChip provider={m.suggested_by.provider} />}
          {m?.options?.profile && <span className="chip ok" title={m.options.profile}>From the {m.options.profile_name} profile</span>}
        </div>
      </div>
      {(info.issues || []).map((i, k) => <Banner key={k} kind={i.level === "error" ? "bad" : "warn"}>{i.message}</Banner>)}
      {unsure.length > 0 && (
        <Banner kind="warn" title="Check these guesses before confirming. The AI was not sure of them:">
          <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
            {unsure.map((h) => <li key={h}><b>{h}</b> → {labelOf(suggestion[h].target)} ({Math.round((suggestion[h].confidence || 0) * 100)}% sure)</li>)}
          </ul>
        </Banner>
      )}
      {m && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Column on screen / in sheet</th><th>Sample values</th><th>Means (payroll field)</th><th>Why suggested</th></tr></thead>
            <tbody>
              {columns.map((h) => (
                <tr key={h}>
                  <td><b>{h}</b></td>
                  <td className="small muted" style={{ maxWidth: 260 }}>{sample(h)}</td>
                  <td>
                    <select className="target" disabled={!isMaker} value={mapping[h] || "ignore"} onChange={(e) => setTarget(h, e.target.value)}>
                      <option value="ignore">— Not used —</option>
                      {Object.entries(groups).map(([g, ts]) => (
                        <optgroup key={g} label={g}>{ts.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</optgroup>
                      ))}
                      {mapping[h]?.startsWith("attendance.unpaid_days@") && <option value={mapping[h]}>Unpaid days in {mapping[h].split("@")[1]}</option>}
                    </select>
                  </td>
                  <td className="small muted">
                    {unsure.includes(h) && <span className="chip warn">check</span>}{" "}
                    {suggestion[h] ? `${Math.round((suggestion[h].confidence || 0) * 100)}% · ${suggestion[h].reason}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {movedToSalary.length > 0 && (
        <p className="muted small" style={{ marginTop: 8 }}>
          Shown with the salary data: {movedToSalary.join(", ")}
        </p>
      )}
      {(table.salary_from_employees || []).map((x) => <SalaryFromEmployees key={x.table} found={x} salaryTable={table.name} />)}
      {Object.keys(valueMaps).length > 0 && (
        <div className="grid2" style={{ marginTop: 12 }}>
          {valueMaps["leave.type"] && (
            <div>
              <div className="group-label">Which leave types cut pay?</div>
              {Object.entries(valueMaps["leave.type"]).map(([v, g]) => (
                <div key={v} className="row" style={{ marginBottom: 4 }}>
                  <span style={{ width: 160 }}>{v}</span>
                  <select disabled={!isMaker} value={g} onChange={(e) => setValueMaps({ ...valueMaps, "leave.type": { ...valueMaps["leave.type"], [v]: e.target.value } })}>
                    <option value="unpaid">Unpaid (cuts pay)</option><option value="paid">Paid (no cut)</option>
                  </select>
                </div>
              ))}
            </div>
          )}
          {valueMaps["leave.breakdown"] && (
            <div>
              <div className="group-label">Which values are a half day?</div>
              {Object.entries(valueMaps["leave.breakdown"]).map(([v, g]) => (
                <div key={v} className="row" style={{ marginBottom: 4 }}>
                  <span style={{ width: 160 }}>{v}</span>
                  <select disabled={!isMaker} value={g} onChange={(e) => setValueMaps({ ...valueMaps, "leave.breakdown": { ...valueMaps["leave.breakdown"], [v]: e.target.value } })}>
                    <option value="half">Half day (0.5)</option><option value="full">Full day (1)</option>
                  </select>
                </div>
              ))}
            </div>
          )}
          {valueMaps["leave.status"] && (
            <div>
              <div className="group-label">Which statuses count?</div>
              {Object.entries(valueMaps["leave.status"]).map(([v, g]) => (
                <div key={v} className="row" style={{ marginBottom: 4 }}>
                  <span style={{ width: 160 }}>{v}</span>
                  <select disabled={!isMaker} value={g} onChange={(e) => setValueMaps({ ...valueMaps, "leave.status": { ...valueMaps["leave.status"], [v]: e.target.value } })}>
                    <option value="count">Counts</option><option value="skip">Ignore</option>
                  </select>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="ghost small" onClick={() => setShowRows(!showRows)}>{showRows ? "Hide" : "Show"} the rows as read</button>
        <span className="spacer" />
        {m && isMaker && (
          <>
            <button className="primary" disabled={!!busy} onClick={confirm}>{m.confirmed ? "Confirm again" : "Confirm mapping"}</button>
          </>
        )}
      </div>
      {showRows && (
        <div className="table-wrap" style={{ marginTop: 10, maxHeight: 360 }}>
          <table><thead><tr>{table.headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i}>{table.headers.map((h) => <td key={h} className="small">{r[h]}</td>)}</tr>)}</tbody></table>
        </div>
      )}
    </div>
  );
}

// Salary figures that were on the employee list, shown with the salary data they belong to.
function SalaryFromEmployees({ found, salaryTable }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rows = found.rows.filter((r) => matches(query, r.employee, ...Object.values(r.values)));
  return (
    <div className="card tight" style={{ marginTop: 12 }}>
      <div className="row between">
        <div>
          <b>Also on the {found.table}: {found.columns.join(", ")}</b>
          <div className="muted small">Not used for pay (pay comes from the {salaryTable})</div>
        </div>
        <button className="ghost small" onClick={() => setOpen(!open)}>{open ? "Hide" : `Show for ${found.rows.length} employees`}</button>
      </div>
      {open && (
        <>
          <TableToolbar query={query} onQuery={setQuery} shown={rows.length} total={found.rows.length} noun="employees" />
          <div className="table-wrap" style={{ maxHeight: 320 }}><table>
            <thead><tr><th>Employee</th>{found.columns.map((c) => <th key={c}>{c} (on the {found.table})</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => <tr key={i}><td>{r.employee}</td>{found.columns.map((c) => <td key={c}>{r.values[c]}</td>)}</tr>)}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}

// =================================================================== snapshot
// Opened from a payroll month (`back` given), the page belongs to that month: its back link returns there.
export function SnapshotPage({ snapshotId, me, back }) {
  const { data: s, error, reload } = useLoad(() => api.get(`/snapshots/${snapshotId}`), [snapshotId]);
  const [tab, setTab] = useState("employees");
  const [busy, act] = useAction();
  const [warningsRead, setWarningsRead] = useState(false);
  if (!s) return <Loading error={error} />;
  const isMaker = me.roles.includes("maker");
  const approve = async () => { if (await act("approve", () => api.post(`/snapshots/${s.id}/approve`, { warnings_read: warningsRead }), "Data reviewed. It can now be used for a month.")) reload(true); };
  const reject = async () => { if (await act("reject", () => api.post(`/snapshots/${s.id}/reject`), "Data rejected")) reload(true); };
  const d = s.data;
  const names = Object.fromEntries(d.employees.map((e) => [e.code, e.name]));
  return (
    <div>
      <div className="topbar">
        <div>
          <a href={back?.href || "#/data"} className="small">← {back?.label || "HR data"}</a>
          <div className="title-row"><h1>Data snapshot #{s.id}</h1><PageHelp topic="hr_snapshot" /></div>
          <div className="row">
            <StatusChip status={s.status === "approved" ? "approved_snapshot" : s.status} label={s.status_label} />
            <span className="muted">from {s.source_system} · {when(s.created_at)}</span>
            <span className="hash" title="Fingerprint of the HR facts (same facts from any source give the same fingerprint)">{s.content_hash.slice(0, 12)}</span>
          </div>
        </div>
        {isMaker && s.status === "pending_review" && (
          <div className="row">
            <button className="danger" disabled={!!busy} onClick={reject}>Reject</button>
            {s.warnings > 0 && s.errors === 0 && (
              <label className="check" title="Warnings often change pay: read them in Checks below">
                <input type="checkbox" checked={warningsRead} onChange={(e) => setWarningsRead(e.target.checked)} /> I have read the {s.warnings} warning{s.warnings > 1 ? "s" : ""}
              </label>
            )}
            <button className="primary" disabled={!!busy || s.errors > 0 || (s.warnings > 0 && !warningsRead)} onClick={approve}>Data looks right</button>
          </div>
        )}
      </div>
      {s.status === "approved" && <Banner kind="ok">Reviewed by {s.reviewed_by?.name} on {when(s.reviewed_at)}.{s.used_by_runs.length ? ` Used by: ${s.used_by_runs.map((r) => r.label).join(", ")}.` : ""}</Banner>}
      {s.errors > 0 && <Banner kind="bad" title={`${s.errors} problem(s) must be fixed at the source.`}>Fix them in the HR tool or the file, then read again.</Banner>}
      <div className="grid2">
        <div className="card"><h2>Checks</h2><Checks checks={s.checks} /></div>
        <div className="card">
          <h2>Unpaid leave</h2>
          {s.unpaid_by_month.periods.length === 0 ? <p className="muted">No unpaid leave in this data.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Employee</th>{s.unpaid_by_month.periods.map((p) => <th key={p} className="num">{monthLabel(p)}</th>)}</tr></thead>
              <tbody>{s.unpaid_by_month.rows.filter((r) => Object.values(r.days).some((v) => v !== "0")).map((r) => (
                <tr key={r.code}><td>{r.name}</td>{s.unpaid_by_month.periods.map((p) => <td key={p} className="num">{r.days[p] === "0" ? "—" : r.days[p]}</td>)}</tr>
              ))}</tbody></table></div>
          )}
          <p className="muted small" style={{ marginTop: 8 }}>Everyone else has no unpaid days.</p>
        </div>
      </div>
      <div className="card">
        <Tabs value={tab} onChange={setTab} tabs={[["employees", `Employees (${d.employees.length})`], ["salary", `Salary (${d.compensation.length})`], ["leave", `Leave (${d.leave.length})`], ["attendance", `Unpaid days given per month (${d.attendance.length})`],
          ...(d.presence ? [["presence", `Days present (${d.presence.length})`]] : [])]} />
        {tab === "presence" && d.presence && <PresenceTab rows={d.presence} range={d.presence_range} names={names} />}
        {tab === "employees" && <EmployeesTab employees={d.employees} />}
        {tab === "salary" && <SalaryTab rows={d.compensation} names={names} />}
        {tab === "leave" && <LeaveTab rows={d.leave} names={names} />}
        {tab === "attendance" && (d.attendance.length === 0 ? <p className="muted">The source gave leave records, not monthly totals.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Employee</th><th>Month</th><th className="num">Unpaid days</th><th>Source</th></tr></thead>
            <tbody>{d.attendance.map((a, i) => <tr key={i}><td>{names[a.employee_code] || a.employee_code}</td><td>{monthLabel(a.period)}</td><td className="num">{a.unpaid_days}</td><td className="small muted">{a.source}</td></tr>)}</tbody>
          </table></div>
        ))}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------- snapshot tabs (searchable for long lists)
const uniq = (xs) => [...new Set(xs.filter(Boolean))].sort();

function EmployeesTab({ employees }) {
  const [query, setQuery] = useState("");
  const [dept, setDept] = useState("");
  const [state, setState] = useState("");
  const depts = uniq(employees.map((e) => e.department));
  const states = uniq(employees.map((e) => e.work_state));
  const rows = employees.filter((e) => (!dept || e.department === dept) && (!state || e.work_state === state)
    && matches(query, e.name, e.code, e.designation));
  return (
    <>
      <TableToolbar query={query} onQuery={setQuery} placeholder="Search by name, ID or designation" shown={rows.length} total={employees.length} noun="employees">
        {depts.length > 1 && <Select value={dept} onChange={setDept} label="Department" options={[["", "All departments"], ...depts.map((x) => [x, x])]} />}
        {states.length > 1 && <Select value={state} onChange={setState} label="State" options={[["", "All states"], ...states.map((x) => [x, stateName(x)])]} />}
      </TableToolbar>
      <div className="table-wrap tall"><table>
        <thead><tr><th>ID</th><th>Name</th><th>Designation</th><th>Department</th><th>State</th><th>Gender</th><th>Joined</th><th>Left</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={8} className="empty">Nobody matches.</td></tr>}
          {rows.map((e) => (
            <tr key={e.code}><td>{e.code}</td><td>{e.name}</td><td>{e.designation || "—"}</td><td>{e.department || "—"}</td>
              <td>{e.work_state || <span className="chip warn">missing</span>}</td><td>{e.gender || "—"}</td>
              <td>{e.date_of_joining ? day(e.date_of_joining) : <span className="chip warn">missing</span>}</td><td>{e.date_of_exit ? day(e.date_of_exit) : "—"}</td></tr>
          ))}
        </tbody>
      </table></div>
    </>
  );
}

// Daily attendance, summed per person per month. The month page uses it to suggest possible unpaid absences.
function PresenceTab({ rows, range, names }) {
  const [query, setQuery] = useState("");
  const shown = rows.filter((r) => matches(query, names[r.employee_code], r.employee_code, r.month));
  return (
    <>
      <p className="muted small">Attendance covers {day(range.from)} to {day(range.to)}</p>
      <TableToolbar query={query} onQuery={setQuery} shown={shown.length} total={rows.length} noun="person-months" />
      <div className="table-wrap tall"><table>
        <thead><tr><th>Employee</th><th>Month</th><th className="num">Days present</th><th>Days of the month</th></tr></thead>
        <tbody>{shown.map((r) => (
          <tr key={`${r.employee_code}-${r.month}`}><td>{names[r.employee_code] || r.employee_code} <span className="muted small">{r.employee_code}</span></td>
            <td>{monthLabel(r.month)}</td><td className="num">{r.days.length}</td><td className="small muted">{r.days.join(", ")}</td></tr>
        ))}</tbody>
      </table></div>
    </>
  );
}

function SalaryTab({ rows, names }) {
  const [query, setQuery] = useState("");
  const [only, setOnly] = useState("all");
  const per = rows.reduce((m, c) => ({ ...m, [c.employee_code]: (m[c.employee_code] || 0) + 1 }), {});
  const shown = rows.filter((c) => (only === "all" || per[c.employee_code] > 1) && matches(query, names[c.employee_code], c.employee_code));
  const raised = Object.values(per).filter((n) => n > 1).length;
  return (
    <>
      <TableToolbar query={query} onQuery={setQuery} shown={shown.length} total={rows.length} noun="salary records">
        <Segmented value={only} onChange={setOnly} options={[["all", "Everyone"], ["changed", "People with a salary change", raised]]} />
      </TableToolbar>
      <div className="table-wrap tall"><table>
        <thead><tr><th>Employee</th><th>From</th><th>Recorded on</th><th className="num">Basic</th><th className="num">HRA</th><th className="num">Special</th><th className="num">Other</th><th className="num">Monthly total</th><th>Source</th></tr></thead>
        <tbody>
          {shown.length === 0 && <tr><td colSpan={9} className="empty">Nothing matches.</td></tr>}
          {shown.map((c, i) => {
            const comp = c.components;
            const other = Object.entries(comp).filter(([k]) => !["basic", "hra", "special_allowance"].includes(k)).reduce((a, [, v]) => a + Number(v), 0);
            const total = Object.values(comp).reduce((a, v) => a + Number(v), 0);
            return <tr key={i}><td>{names[c.employee_code] || c.employee_code}<div className="muted small">{c.employee_code}</div></td><td>{day(c.effective_from)}</td>
              <td>{c.recorded_on ? day(c.recorded_on) : "—"}{c.recorded_on_assumed && <span className="chip warn" style={{ marginLeft: 4 }} title="The source gave no date; the date it was first read is used">first seen</span>}</td>
              <td className="num">{inr(comp.basic)}</td><td className="num">{inr(comp.hra)}</td><td className="num">{inr(comp.special_allowance)}</td><td className="num">{other ? inr(other) : "—"}</td><td className="num"><b>{inr(total)}</b></td><td className="small muted">{c.source}</td></tr>;
          })}
        </tbody>
      </table></div>
    </>
  );
}

function LeaveTab({ rows, names }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [only, setOnly] = useState("all");
  const types = uniq(rows.map((l) => l.leave_type));
  const shown = rows.filter((l) => (!type || l.leave_type === type) && (only === "all" || l.unpaid) && matches(query, names[l.employee_code], l.employee_code));
  return (
    <>
      <TableToolbar query={query} onQuery={setQuery} shown={shown.length} total={rows.length} noun="leave records">
        <Segmented value={only} onChange={setOnly} options={[["all", "All leave"], ["unpaid", "Cuts pay", rows.filter((l) => l.unpaid).length]]} />
        {types.length > 1 && <Select value={type} onChange={setType} label="Leave type" options={[["", "All types"], ...types.map((x) => [x, x])]} />}
      </TableToolbar>
      <div className="table-wrap tall"><table>
        <thead><tr><th>Employee</th><th>Type</th><th>From</th><th>To</th><th className="num">Days</th><th>Status</th><th>Cuts pay?</th></tr></thead>
        <tbody>
          {shown.length === 0 && <tr><td colSpan={7} className="empty">Nothing matches.</td></tr>}
          {shown.map((l, i) => <tr key={i}><td>{names[l.employee_code] || l.employee_code}<div className="muted small">{l.employee_code}</div></td><td>{l.leave_type || "—"}</td><td>{day(l.start_date)}</td><td>{day(l.end_date)}</td><td className="num">{l.days}</td><td>{l.status || "—"}</td><td>{l.unpaid ? <span className="chip warn">Unpaid</span> : <span className="chip">Paid</span>}</td></tr>)}
        </tbody>
      </table></div>
      <p className="muted small" style={{ padding: "8px 2px 0" }}>Requests that were not approved are left out.</p>
    </>
  );
}
