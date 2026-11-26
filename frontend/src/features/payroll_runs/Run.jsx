import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../shared/api.js";
import { go } from "../../shared/routing.js";
import { DuesTable } from "../dues/Dues.jsx";
import {
  AIChip, Banner, Drawer, Field, Loading, Segmented, Select, Stat, StatusChip, TableToolbar, day, inr, isModel, matches, signed, stateName, toast,
  useAction, useLoad, usePoll, when,
} from "../../shared/ui.jsx";

const SCOPE_LABEL = { statutory: "Government rule", company: "Company setting", month: "Set for this month", override: "What-if value", default: "Built-in default" };

export default function RunPage({ runId, me }) {
  const { data: run, error, reload } = useLoad(() => api.get(`/runs/${runId}`), [runId]);
  const [waitingFor, setWaitingFor] = useState(null); // {from: status, until: time}
  const [busy, act] = useAction();
  const [drawer, setDrawer] = useState(null);
  const poll = useCallback(async () => {
    const r = await reload(true);
    if (r && waitingFor && (r.status !== waitingFor.from || r.calculation_count !== waitingFor.count || r.last_error !== waitingFor.err || Date.now() > waitingFor.until)) {
      setWaitingFor(null);
      if (r.last_error && r.last_error !== waitingFor.err) toast(r.last_error, "bad");
    }
  }, [reload, waitingFor]);
  usePoll(poll, !!waitingFor, 900);

  if (!run) return <Loading error={error} />;
  const isMaker = me.roles.includes("maker");
  const isChecker = me.roles.includes("checker");
  const draft = run.run_type === "draft";

  const doAction = async (action, body = {}, ok) => {
    const out = await act(action, () => api.post(`/runs/${run.id}/${action}`, body), ok);
    if (!out) return;
    if (out.outcome?.queued) {
      setWaitingFor({ from: run.status, count: run.calculation_count, err: run.last_error, until: Date.now() + 30000 });
      toast("Sent to the workflow. This page updates when it is done.", "info");
    }
    await reload(true);
    return out;
  };

  const employees = run.result?.employees || [];
  const changedCodes = new Set((run.comparison?.employees || []).filter((e) => e.status !== "unchanged").map((e) => e.code));
  const openExplain = (code, focus) => setDrawer({ code, focus });

  return (
    <div>
      <div className="topbar">
        <div>
          <a href="#/months" className="small">← Payroll months</a>
          <h1>{run.period_label}{draft && <span className="muted"> · draft re-run #{run.id}</span>}</h1>
          <div className="row">
            <StatusChip status={run.status} label={run.status_label} />
            <WorkflowBadge wf={run.workflow} />
            {run.result_hash && <span className="hash" title="Fingerprint of the result. Same inputs always give the same fingerprint.">result {run.result_hash.slice(0, 10)}</span>}
            {waitingFor && <span className="row small muted"><span className="spinner" /> waiting for the workflow…</span>}
          </div>
        </div>
        <div className="row">
          <button onClick={() => reload(true)}>Refresh</button>
        </div>
      </div>

      {draft && run.base_run && (
        <Banner kind="info" title="This is a what-if.">
          It re-runs {run.base_run.label} (#{run.base_run.id}, {run.base_run.status_label.toLowerCase()}) with today's rules
          {Object.keys(run.policy_overrides || {}).length ? " and these what-if settings" : ""}. The original month is not changed. <a href={`#/runs/${run.base_run.id}`}>Open the original</a>.
          {Object.entries(run.policy_overrides || {}).map(([key, values]) => (
            <div key={key} className="small" style={{ marginTop: 4 }}>
              <span className="chip warn">what-if</span> {key}: {Object.entries(values).map(([k, v]) => `${k} = ${v}`).join(", ")}
            </div>
          ))}
        </Banner>
      )}
      {!draft && <Stepper steps={run.steps} />}
      <NextAction run={run} me={me} />
      {run.last_error && !["finalized"].includes(run.status) && <Banner kind="bad" title="Last action was refused:">{run.last_error}</Banner>}
      {run.newer_hr_data && (
        <Banner kind="warn" title="HR data changed after this month was prepared."
          action={run.can.use_latest_data && <button onClick={() => doAction("use-latest-data", {}, "Now using the latest HR data")}>Use the new data</button>}>
          Data #{run.newer_hr_data.id} ({run.newer_hr_data.source_system}, {when(run.newer_hr_data.reviewed_at)}) differs for this month. It cannot be approved until it is recalculated with the new data.
        </Banner>
      )}
      {run.freshness && !run.freshness.fresh && !run.newer_hr_data && (
        <Banner kind="warn" title="Needs to be calculated again:">{run.freshness.reasons.join("; ")}.</Banner>
      )}
      {run.four_eyes_note && <Banner kind="warn">{run.four_eyes_note}</Banner>}
      <EmployeeNotes issues={(run.result?.issues || []).filter((i) => i.level !== "info")} employees={employees} onOpen={(code) => openExplain(code, "net_pay")} />

      {!draft && <DataSection run={run} busy={busy} onAction={doAction} />}
      {!draft && <ChangesSection run={run} onChange={() => reload(true)} />}

      {run.result ? (
        <>
          <Comparison run={run} onExplain={openExplain} />
          <Register run={run} changedCodes={changedCodes} onExplain={openExplain} />
        </>
      ) : (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="empty">
            Not calculated yet.
            {run.can.calculate && <div style={{ marginTop: 10 }}><button className="primary" disabled={!!busy || !!waitingFor} onClick={() => doAction("calculate")}>Calculate {run.period_label}</button></div>}
          </div>
        </div>
      )}

      {!draft && <ApprovalSection run={run} busy={busy || waitingFor} onAction={doAction} isChecker={isChecker} isMaker={isMaker} />}
      {!draft && <FilesSection run={run} busy={busy || waitingFor} onAction={doAction} />}
      {!draft && run.dues.length > 0 && <DuesSection run={run} me={me} onChange={reload} />}
      {run.result && <WhatIf run={run} me={me} />}
      {run.result && <Repeatable run={run} />}
      <Events run={run} />

      {drawer && (
        <ExplainDrawer
          run={run}
          employee={employees.find((e) => e.code === drawer.code)}
          focus={drawer.focus}
          comparison={run.comparison?.employees.find((e) => e.code === drawer.code)}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

// Notes the calculation made about individual people, in one place (errors first), short when there are many.
function EmployeeNotes({ issues, employees, onOpen }) {
  const [all, setAll] = useState(false);
  if (!issues.length) return null;
  const sorted = [...issues].sort((a, b) => (a.level === "error" ? 0 : 1) - (b.level === "error" ? 0 : 1));
  const errors = issues.filter((i) => i.level === "error").length;
  const shown = all ? sorted : sorted.slice(0, 4);
  const name = (code) => employees.find((e) => e.code === code)?.name || code;
  return (
    <Banner kind={errors ? "bad" : "warn"} title={`${issues.length} note${issues.length > 1 ? "s" : ""} about individual employees${errors ? ` (${errors} must be fixed)` : ""}:`}>
      <ul className="notes-list">
        {shown.map((i, k) => (
          <li key={k}><a href="#" onClick={(e) => { e.preventDefault(); onOpen(i.employee); }}><b>{name(i.employee)}</b></a>: {i.message}</li>
        ))}
      </ul>
      {sorted.length > 4 && <button className="ghost small" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${sorted.length}`}</button>}
    </Banner>
  );
}

function WorkflowBadge({ wf }) {
  if (!wf) return null;
  if (wf.backend === "temporal") {
    const state = wf.state;
    const text = wf.reachable === false ? "Temporal not reachable" : `Temporal workflow ${wf.status ? wf.status.toLowerCase() : ""}`;
    return (
      <a className={`chip ${wf.reachable === false ? "bad" : "brand"}`} href={wf.temporal_ui || "#"} target="_blank" rel="noreferrer"
        title={`${wf.workflow_id}${state ? ` · ${state.pending_commands} pending` : ""}`}>⟳ {text}</a>
    );
  }
  if (wf.backend === "none") return <span className="chip">what-if (no workflow)</span>;
  return <span className="chip" title="Steps run in-process; state is kept in the database">local workflow</span>;
}

function Stepper({ steps }) {
  if (!steps?.length) return null;
  const current = steps.findIndex((s) => !s.done);
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div key={s.key} className={`step ${s.done ? "done" : ""} ${i === current ? "current" : ""}`}>
          <span className="b">{s.done ? "✓" : i + 1}</span>{s.label}
        </div>
      ))}
    </div>
  );
}

function NextAction({ run, me }) {
  const isMaker = me.roles.includes("maker");
  const isChecker = me.roles.includes("checker");
  let text = null;
  let kind = "info";
  if (run.run_type === "draft") return null;
  if (run.status === "finalized") { text = `Final. Approved by ${run.decided_by?.name} on ${when(run.decided_at)}. The register and payment files are below.`; kind = "ok"; }
  else if (run.status === "awaiting_approval") {
    text = isChecker && !run.four_eyes_note ? "Waiting for you. Check what changed and approve, or send it back with a reason."
      : `Submitted by ${run.submitted_by?.name} on ${when(run.submitted_at)}. Waiting for the finance head. It keeps waiting through restarts.`;
    kind = "warn";
  } else if (run.status === "rejected") { text = `Sent back by ${run.decided_by?.name}: "${run.decision_note}". Make the corrections, calculate and submit again.`; kind = "bad"; }
  else if (isMaker) {
    if (!run.data_reviewed_at) text = "Step 1: review the HR data that was read, then mark it as reviewed.";
    else if (run.pending_changes) text = "Confirm or discard the proposed changes.";
    else if (run.status === "draft") text = "Calculate the month. You can add changes in plain English first.";
  } else text = "This month is being prepared by a finance team employee.";
  return text ? <Banner kind={kind}>{text}</Banner> : null;
}

function SectionTitle({ n, title, right }) {
  return (
    <div className="section-title">
      {n && <span className="n">{n}</span>}
      <h2>{title}</h2>
      <span className="spacer" />
      {right}
    </div>
  );
}

// ------------------------------------------------------------------ 1. data
function DayCount({ run, busy, onAction }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(run.day_count.days));
  const dc = run.day_count;
  const save = async () => { if (await onAction("day-count", { day_count: value }, `One day's pay is now monthly pay ÷ ${value}`)) setEditing(false); };
  return (
    <div className="card row between">
      <div>
        <b>One day's pay = monthly pay ÷ {dc.days}</b> <span className={`chip ${dc.set_for_month ? "brand" : ""}`}>{dc.set_for_month ? `set for ${run.period_label}` : dc.source}</span>
      </div>
      {run.can.change_day_count && (editing ? (
        <div className="row">
          <input type="number" min="1" max="31" style={{ width: 80 }} value={value} onChange={(e) => setValue(e.target.value)} aria-label="Days for one day's pay" />
          <button className="primary" disabled={!!busy || !value} onClick={save}>Save</button>
          <button className="ghost" onClick={() => { setEditing(false); setValue(String(dc.days)); }}>Cancel</button>
        </div>
      ) : <button onClick={() => setEditing(true)}>Change</button>)}
    </div>
  );
}

function DataSection({ run, busy, onAction }) {
  const s = run.snapshot;
  return (
    <>
      <SectionTitle n="1" title="HR data" />
      <div className="card row between">
        <div>
          <b>Data #{s.id}</b> from {s.source_system} · {s.employee_count} employees · read {when(s.created_at)}
          <div className="muted small">
            Salary changes recorded up to {day(run.knowledge_date)} are used.{" "}
            {s.errors ? <span className="chip bad">{s.errors} errors</span> : s.warnings ? <span className="chip warn">{s.warnings} warnings</span> : <span className="chip ok">all checks passed</span>}
          </div>
        </div>
        <div className="row">
          <button onClick={() => go(`/runs/${run.id}/data/${s.id}`)}>See the data</button>
          {run.data_reviewed_at
            ? <span className="chip ok">✓ Reviewed by {run.data_reviewed_by?.name}</span>
            : run.can.review_data && <button className="primary" disabled={!!busy} onClick={() => onAction("review-data", {}, "Data marked as reviewed")}>I have reviewed the data</button>}
        </div>
      </div>
      {run.run_type !== "draft" && <DayCount key={`${run.day_count.days}-${run.status}`} run={run} busy={busy} onAction={onAction} />}
    </>
  );
}

// ------------------------------------------------------------------ 2. changes

// Speech to text with the browser's own recognition (Chrome, Edge, Safari). Words fill the box; nothing is sent until the person presses the button.
const SpeechRecognition = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;

function VoiceInput({ onText }) {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const rec = useRef(null);
  useEffect(() => () => rec.current?.abort(), []);
  const start = () => {
    if (!SpeechRecognition) {
      toast("This browser has no speech recognition (Firefox, for example). Open the app in Chrome or Edge, or type the change.", "bad");
      return;
    }
    const r = new SpeechRecognition();
    r.lang = "en-IN"; // Indian English: names, rupees and lakhs are heard better
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => {
      let done = "", partial = "";
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        if (e.results[i].isFinal) done += e.results[i][0].transcript; else partial += e.results[i][0].transcript;
      }
      setInterim(partial);
      if (done.trim()) onText(done.trim());
    };
    r.onerror = (e) => {
      // Every failure is said out loud: a button that just switches off looks like "audio not working".
      const why = {
        "not-allowed": "Allow the microphone for this site in your browser, then try again.",
        "service-not-allowed": "This browser does not allow speech recognition here. Open the app in Chrome or Edge.",
        "network": "Speech recognition needs the browser's online speech service, which this browser cannot reach (for example the browser built into VS Code). Open the app in Chrome or Edge.",
        "audio-capture": "No microphone was found. Connect one, or check it is not used by another app.",
        "no-speech": "Didn't hear anything. Try again, a little closer to the microphone.",
        "language-not-supported": "This browser cannot recognise Indian English. Open the app in Chrome or Edge.",
        "aborted": "",
      }[e.error];
      if (why !== "") toast(why || `Speech recognition stopped (${e.error}). Type the change instead.`, "bad");
    };
    r.onend = () => { setListening(false); setInterim(""); };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch (err) {
      toast(`Could not start the microphone (${err.message}). Open the app in Chrome or Edge, or type the change.`, "bad");
    }
  };
  const stop = () => rec.current?.stop();
  return (
    <>
      {listening && <span className="voice-interim" aria-live="polite">{interim || "Listening…"}</span>}
      <button type="button" className={`voice-btn${listening ? " is-on" : ""}`} onClick={listening ? stop : start}
        aria-pressed={listening} aria-label={listening ? "Stop listening" : "Speak a change"} title={listening ? "Stop listening" : "Speak a change"}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
      </button>
    </>
  );
}

function ChangesSection({ run, onChange }) {
  const [text, setText] = useState("");
  const [busy, act] = useAction();
  const propose = async (e) => {
    e?.preventDefault();
    const ch = await act("propose", () => api.post(`/runs/${run.id}/instructions`, { text }));
    if (ch) { setText(""); onChange(); }
  };
  const decide = async (id, decision) => {
    if (await act(decision, () => api.post(`/changes/${id}/${decision}`), decision === "confirm" ? "Change confirmed. Calculate again to include it." : "Change discarded")) onChange();
  };
  const ask = async (c) => {
    const ch = await act("again", () => api.post(`/runs/${run.id}/instructions`, { text: c.text, force: true }));
    if (ch) { await api.post(`/changes/${c.id}/discard`).catch(() => {}); onChange(); }
  };
  const [mode, setMode] = useState("form");
  return (
    <>
      <SectionTitle n="2" title="Changes this month" />
      <div className="card">
        {run.can.add_changes && (
          <>
            <div style={{ marginBottom: 10 }}>
              <Segmented value={mode} onChange={setMode} options={[["form", "Add by hand"], ["type", (
                <span className="seg-icon" title="Type or speak a sentence">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
                    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
                  <span className="sr-only">Type or speak a sentence</span>
                </span>
              )]]} />
            </div>
            {mode === "form" ? <AddByHand run={run} onAdded={onChange} /> : (
              <>
                <form className="instruction" onSubmit={propose}>
                  <div className="instruction-field">
                    <input placeholder='Type or speak a change, e.g. "Give Priya a ₹10,000 one-time bonus this month"' value={text} onChange={(e) => setText(e.target.value)} />
                    <VoiceInput onText={(spoken) => setText((t) => (t.trim() ? `${t.trim()} ${spoken}` : spoken))} />
                  </div>
                  <button className="primary" disabled={!text.trim() || !!busy}>{busy === "propose" ? "Reading…" : "Check what it means"}</button>
                </form>
              </>
            )}
          </>
        )}
        {run.can.add_changes && <PossibleAbsences run={run} onAdded={onChange} />}
        {run.changes.map((c) => (
          <div key={c.id} className="understood">
            <div className="row between">
              <span className="muted small">{c.proposal.entered_by_hand ? c.text : `“${c.text}”`} · {c.created_by?.name} · {when(c.created_at)}</span>
              <span className="row">{c.proposal.entered_by_hand ? <span className="chip">Entered by hand</span> : c.ai && <AIChip provider={c.ai.provider} />}
                {c.ai?.reused_saved_answer && <span className="chip">saved answer reused</span>}
                <StatusChip status={c.status === "confirmed" ? "accepted" : "pending_review"} label={c.status === "confirmed" ? "Confirmed" : "Proposed"} /></span>
            </div>
            {!c.proposal.entered_by_hand && <div className="muted small" style={{ marginTop: 6 }}>The system understood:</div>}
            {c.proposal.items.map((it, i) => <div key={i} className="item">{it.summary}</div>)}
            {c.proposal.problems.map((p, i) => <Banner key={i} kind="bad">{p}</Banner>)}
            {(c.proposal.notes || []).map((n, i) => <div key={i} className="muted small" style={{ marginBottom: 6 }}>Note from the AI: {n}</div>)}
            {c.status === "proposed" && run.can.add_changes && (
              <div className="row">
                <button className="good" disabled={!!busy || c.proposal.problems.length > 0} onClick={() => decide(c.id, "confirm")}>Yes, that's right</button>
                <button className="danger" disabled={!!busy} onClick={() => decide(c.id, "discard")}>Discard</button>
                {isModel(c.ai?.provider) && <button className="ghost small" disabled={!!busy} onClick={() => ask(c)}>Ask the AI again</button>}
              </div>
            )}
            {c.status === "confirmed" && <div className="muted small">Confirmed by {c.decided_by?.name} · {when(c.decided_at)}{run.can.add_changes && <> · <button className="ghost small" onClick={() => decide(c.id, "discard")}>remove</button></>}</div>}
          </div>
        ))}
      </div>
    </>
  );
}

// Working days with no check-in and no approved leave, from the attendance in the HR data. Only suggestions: the
// finance team employee adds each as an unpaid absence (the same entry as "Add by hand") or says it was not one, with a reason.
function PossibleAbsences({ run, onAdded }) {
  const key = `${run.id}-${run.changes.length}-${run.snapshot.id}`;
  const { data, error, reload } = useLoad(() => api.get(`/runs/${run.id}/possible-absences`), [key]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(null); // [index, "add" | "dismiss"]
  const [reason, setReason] = useState("");
  const [busy, act] = useAction();
  if (!data) return error ? <Banner kind="bad">{error}</Banner> : null;
  if (data.status !== "ok") return <p className="muted small" style={{ marginTop: 12 }}>Attendance: {data.message}</p>;
  const rows = data.absences.filter((a) => matches(query, a.name, a.employee_code, a.label));
  const start = (i, mode, a) => { setOpen([i, mode]); setReason(mode === "add" ? a.suggested_reason : ""); };
  const add = async (a) => {
    const ok = await act("add", () => api.post(`/runs/${run.id}/add-by-hand`, { kind: "extra_unpaid_days", employee_code: a.employee_code, dates: a.dates, reason }),
      `Unpaid absence added for ${a.name}. Calculate again to include it.`);
    if (ok) { setOpen(null); onAdded(); }
  };
  const dismiss = async (a) => {
    const ok = await act("dismiss", () => api.post(`/runs/${run.id}/possible-absences/dismiss`, { employee_code: a.employee_code, dates: a.dates, reason }),
      `Kept as worked: ${a.name}, ${a.label}.`);
    if (ok) { setOpen(null); reload(true); }
  };
  return (
    <div className="card tight" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h3 style={{ margin: 0 }}>Possible unpaid absences (from attendance)</h3>
      </div>
      {data.no_check_in.length > 0 && (
        <Banner kind="warn" title={`${data.no_check_in.length} ${data.no_check_in.length > 1 ? "people have" : "person has"} no check-in at all:`}>
          {data.no_check_in.map((p) => `${p.name} (${p.employee_code}, ${p.working_days} working days)`).join(", ")}.
          This is more likely someone who does not use the attendance system than a month of absence. Check, and use “Add by hand” if they were absent.
        </Banner>
      )}
      {data.figure_given.length > 0 && (
        <p className="muted small">Not checked: {data.figure_given.map((p) => p.name).join(", ")} (their unpaid days for the month are given as a figure).</p>
      )}
      {data.absences.length > 0 && (
        <>
          <TableToolbar query={query} onQuery={setQuery} shown={rows.length} total={data.absences.length} noun="suggestions" />
          <div className="table-wrap"><table>
            <thead><tr><th>Employee</th><th>Days with no check-in</th><th className="num">Working days</th><th></th></tr></thead>
            <tbody>{rows.map((a) => {
              const i = data.absences.indexOf(a);
              return (
                <React.Fragment key={`${a.employee_code}-${a.dates[0]}`}>
                  <tr>
                    <td>{a.name} <span className="muted small">{a.employee_code}</span></td>
                    <td>{a.label}</td>
                    <td className="num">{a.days}</td>
                    <td className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="small" onClick={() => start(i, "add", a)}>Add as unpaid absence</button>
                      <button className="ghost small" onClick={() => start(i, "dismiss", a)}>Not an absence</button>
                    </td>
                  </tr>
                  {open && open[0] === i && (
                    <tr><td colSpan={4} style={{ background: "var(--line-2)" }}>
                      <div className="row" style={{ alignItems: "flex-end" }}>
                        <Field label={open[1] === "add" ? `Reason for ${a.days} unpaid day${a.days > 1 ? "s" : ""}` : "Why it was not an absence"}>
                          <input style={{ minWidth: 340 }} value={reason} onChange={(e) => setReason(e.target.value)}
                            placeholder={open[1] === "add" ? "" : "e.g. forgot to punch; was at the client's site"} />
                        </Field>
                        {open[1] === "add"
                          ? <button className="primary" disabled={!!busy || !reason.trim()} onClick={() => add(a)}>Add unpaid absence</button>
                          : <button disabled={!!busy || !reason.trim()} onClick={() => dismiss(a)}>Not an absence</button>}
                        <button className="ghost" onClick={() => setOpen(null)}>Cancel</button>
                      </div>
                    </td></tr>
                  )}
                </React.Fragment>
              );
            })}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}

// A change HR enters directly: an unpaid absence (for example absent without approval, which no HR tool records
// as leave), a one-time payment or a deduction. No AI; every value is typed by a person.
const PAY_ITEMS = [["bonus", "Bonus"], ["incentive", "Incentive"], ["commission", "Commission"], ["overtime", "Overtime"], ["reimbursement", "Reimbursement"], ["other_earning", "Other payment"]];
const DEDUCTION_ITEMS = [["advance_recovery", "Advance recovery"], ["loan_recovery", "Loan recovery"], ["other_deduction", "Other deduction"]];
function AddByHand({ run, onAdded }) {
  const blank = { kind: "one_time_earning", who: "", days: "", from: "", to: "", reason: "", item: "bonus", amount: "", taxable: true,
    month: (run.paid_months || [])[0]?.period || "", effective_from: "", direction: "add" };
  const [f, setF] = useState(blank);
  const [busy, act] = useAction();
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const people = run.people || [];
  const label = (p) => `${p.name} (${p.code})`;
  const person = people.find((p) => label(p) === f.who || p.code.toLowerCase() === f.who.trim().toLowerCase());
  const lateAbsence = f.kind === "past_unpaid_days";
  const absence = f.kind === "extra_unpaid_days" || lateAbsence;
  const salary = f.kind === "salary_revision";
  const bounds = (period) => {
    const [y, m] = period.split("-").map(Number);
    return [`${period}-01`, `${period}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`];
  };
  const [first, last] = bounds(lateAbsence && f.month ? f.month : run.period);
  const paidMonths = run.paid_months || [];
  // Unpaid days follow the dates: From–To counts every calendar day in between (both ends included).
  // The count can be half a day less, for a half day at one end; anything else does not match the dates.
  const DAY = 86400000;
  const span = (from, to) => (from ? Math.round((Date.parse(to || from) - Date.parse(from)) / DAY) + 1 : null);
  const addDays = (iso, n) => new Date(Date.parse(iso) + n * DAY).toISOString().slice(0, 10);
  const rangeDays = absence ? span(f.from, f.to) : null;
  const daysNum = Number(f.days);
  const daysMatch = !rangeDays || (f.days !== "" && (daysNum === rangeDays || daysNum === rangeDays - 0.5));
  const setFrom = (e) => {
    const from = e.target.value;
    if (!from) return setF({ ...f, from: "", to: "" });
    // Days already typed: the absence ends that many days later (kept inside the month). Otherwise keep To if it still fits.
    let to = f.to && f.to >= from ? f.to : "";
    if (f.days && daysNum > 0 && !(f.to && f.to >= from)) { to = addDays(from, Math.ceil(daysNum) - 1); if (to > last) to = last; }
    const n = span(from, to);
    setF({ ...f, from, to, days: String(daysNum === n - 0.5 ? daysNum : n) });  // keeps a half day already typed
  };
  const setTo = (e) => {
    const to = e.target.value;
    const n = span(f.from, to);
    setF({ ...f, to, days: String(daysNum === n - 0.5 ? daysNum : n) });
  };
  const submit = async (e) => {
    e.preventDefault();
    const base = { kind: f.kind, employee_code: person?.code, reason: f.reason };
    const body = absence ? { ...base, days: f.days, from: f.from, to: f.to || f.from, month: lateAbsence ? f.month : undefined, direction: lateAbsence ? f.direction : undefined }
      : salary ? { ...base, amount: f.amount, effective_from: f.effective_from }
      : { ...base, item: f.item, amount: f.amount, taxable: f.kind === "one_time_earning" ? f.taxable : undefined };
    const done = { extra_unpaid_days: "Unpaid absence added.", past_unpaid_days: "Added: it is recovered in this month.",
      salary_revision: "Salary change added. Arrears for months already paid follow." }[f.kind] || "Added.";
    const out = await act("add", () => api.post(`/runs/${run.id}/add-by-hand`, body), `${done} Calculate again to include it.`);
    if (out) { setF({ ...blank, kind: f.kind, month: f.month }); onAdded(); }
  };
  const ready = person && (absence ? f.days && daysMatch && f.reason.trim() && (!lateAbsence || f.month) : salary ? f.amount && f.effective_from : f.amount);
  return (
    <form onSubmit={submit} className="stack">
      <div className="grid3">
        <Field label="What">
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, from: "", to: "", item: e.target.value === "one_time_deduction" ? "advance_recovery" : "bonus" })}>
            {/* By hand: the three everyday changes. A salary change or a correction of a paid month can still be typed as an instruction. */}
            <option value="one_time_earning">One-time bonus</option>
            <option value="one_time_deduction">Deduction</option>
            <option value="extra_unpaid_days">Unpaid leave</option>
          </select>
        </Field>
        <Field label="Employee" hint={f.who && !person ? "Pick a name from the list" : person ? `${person.code}${person.work_state ? " · " + person.work_state : ""}` : `${people.length} people in ${run.period_label}`}>
          <input list={`people-${run.id}`} value={f.who} onChange={set("who")} placeholder="Start typing a name or ID" />
          <datalist id={`people-${run.id}`}>{people.map((p) => <option key={p.code} value={label(p)} />)}</datalist>
        </Field>
        {absence ? (
          <Field label="Unpaid days" hint={!rangeDays ? "Whole or half days"
            : daysMatch ? `${rangeDays} day${rangeDays > 1 ? "s" : ""} from the dates${daysNum === rangeDays - 0.5 ? ", one of them a half day" : ""}`
            : `The dates cover ${rangeDays} day${rangeDays > 1 ? "s" : ""}: enter ${rangeDays} or ${rangeDays - 0.5}, or change the dates`}>
            <input type="number" min="0.5" step="0.5" max={rangeDays || undefined} value={f.days} onChange={set("days")}
              aria-invalid={!daysMatch || undefined} /></Field>
        ) : salary ? (
          <Field label="New monthly salary (₹)" hint="Split into basic, HRA and special allowance by the company setting"><input inputMode="decimal" value={f.amount} onChange={set("amount")} /></Field>
        ) : (
          <Field label="Kind"><select value={f.item} onChange={set("item")}>{(f.kind === "one_time_earning" ? PAY_ITEMS : DEDUCTION_ITEMS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        )}
      </div>
      <div className="grid3">
        {lateAbsence && (
          <Field label="Correction">
            <select value={f.direction} onChange={set("direction")}>
              <option value="add">Add unpaid days (absent, found later): recover the pay</option>
              <option value="remove">Remove unpaid days (counted by mistake): pay back</option>
            </select>
          </Field>
        )}
        {lateAbsence && (
          <Field label="Month it happened (already paid)">
            <select value={f.month} onChange={(e) => setF({ ...f, month: e.target.value, from: "", to: "" })}>
              {paidMonths.map((pm) => <option key={pm.period} value={pm.period}>{pm.label}</option>)}
            </select>
          </Field>
        )}
        {salary ? (
          <Field label="Starts on" hint={`If it starts before ${run.period_label}, months already paid get arrears`}>
            <input type="date" max={last} value={f.effective_from} onChange={set("effective_from")} />
          </Field>
        ) : absence ? (
          <>
            <Field label="From (optional)"><input type="date" min={first} max={last} value={f.from} onChange={setFrom} /></Field>
            <Field label="To (optional)" hint={f.from ? "Leave empty for a single day" : undefined}><input type="date" min={f.from || first} max={last} value={f.to} onChange={setTo} disabled={!f.from} /></Field>
          </>
        ) : (
          <>
            <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={set("amount")} /></Field>
            {f.kind === "one_time_earning" && <label className="check" style={{ alignSelf: "center" }}><input type="checkbox" checked={f.taxable} onChange={set("taxable")} /> Taxable</label>}
          </>
        )}
        <Field label={absence ? "Reason (kept in the audit trail)" : "Reason (optional)"}>
          <input value={f.reason} onChange={set("reason")} placeholder={lateAbsence && f.direction === "remove" ? "e.g. Approved sick leave, counted as unpaid by mistake" : absence ? "e.g. Absent without approval" : salary ? "e.g. Promotion to Senior Designer" : "e.g. Festival bonus"} />
        </Field>
      </div>
      <div className="row">
        <button className="primary" disabled={!ready || !!busy}>
          {busy ? "Adding…" : lateAbsence ? (f.direction === "remove" ? "Pay these days back" : "Recover these days") : absence ? "Add unpaid absence" : salary ? "Add salary change" : f.kind === "one_time_earning" ? "Add bonus" : "Add deduction"}
        </button>
        <span className="muted small">
          {lateAbsence ? `That month stays as paid; the pay for these days is worked out with its rules and ${f.direction === "remove" ? "paid back" : "recovered"} in this month. `
            : f.kind === "extra_unpaid_days" ? "Added on top of the unpaid leave in the HR data. "
            : salary ? "Keeps counting in later months. Enter it in the HR tool too, so the next read agrees. " : ""}
        </span>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ 3. what changed
function Comparison({ run, onExplain }) {
  const cmp = run.comparison;
  const [ai, setAi] = useState(null);
  const [busy, act] = useAction();
  const [open, setOpen] = useState({});
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const t = (code) => cmp?.totals.find((x) => x.code === code);
  const explain = async (force) => {
    const out = await act("explain", () => api.post(`/runs/${run.id}/explain`, { force }));
    if (out) setAi(out);
  };
  if (!cmp) {
    return (
      <>
        <SectionTitle n="3" title="Summary" />
        <div className="grid4">
          <Stat label="Gross pay" value={inr(run.result.totals.gross)} />
          <Stat label="Deductions" value={inr(run.result.totals.total_deductions)} />
          <Stat label="Net pay" value={inr(run.result.totals.net_pay)} />
          <Stat label="Cost to company" value={inr(run.result.totals.employer_cost)} />
        </div>
      </>
    );
  }
  const changedAll = cmp.employees.filter((e) => e.status !== "unchanged");
  const LIMIT = 8;
  const searched = changedAll.filter((e) => matches(query, e.name, e.code));
  const changed = showAll || query ? searched : searched.slice(0, LIMIT);
  const label = run.run_type === "draft" ? `What changes against ${cmp.before_label}` : `What changed since ${cmp.before_label}`;
  return (
    <>
      <SectionTitle n="3" title={label} right={<span className="muted small">{changedAll.length} of {cmp.employees.length} employees paid differently</span>} />
      <div className="grid4">
        {["gross", "total_deductions", "net_pay", "employer_cost"].map((c) => (
          <Stat key={c} label={{ gross: "Gross pay", total_deductions: "Deductions", net_pay: "Net pay", employer_cost: "Cost to company" }[c]}
            value={inr(t(c)?.after)} delta={t(c)?.delta} hint={`vs ${cmp.before_label}`} />
        ))}
      </div>
      {cmp.rule_changes.length > 0 && (
        <Banner kind="info" title="Rules or settings differ:">
          {cmp.rule_changes.map((r, i) => (
            <span key={i}>{i ? "; " : ""}{r.title}{r.jurisdiction ? ` (${r.jurisdiction})` : ""}: {r.before ? `from ${r.before.effective_from || "default"}` : "none"} → {r.after ? (r.after.scope === "override" ? "what-if value" : `version from ${r.after.effective_from}`) : "none"}</span>
          ))}
        </Banner>
      )}
      <div className="card" style={{ marginTop: 12 }}>
        {changedAll.length > LIMIT && <TableToolbar query={query} onQuery={setQuery} shown={searched.length} total={changedAll.length} noun="paid differently" />}
        {changedAll.length === 0 && <div className="empty">Nobody&apos;s pay changed.</div>}
        {changedAll.length > 0 && changed.length === 0 && <div className="empty">Nobody matches the search.</div>}
        {changed.map((e) => (
          <div key={e.code} className="diffrow">
            <div>
              <div className="row"><b>{e.name}</b><span className="muted small">{e.code}</span></div>
              <ul className="drivers">{e.drivers.map((d, i) => <li key={i}>{d.text}</li>)}</ul>
              {e.changes.length > 0 && <button className="ghost small" onClick={() => setOpen({ ...open, [e.code]: !open[e.code] })}>{open[e.code] ? "Hide" : "Show"} the {e.changes.length} amount{e.changes.length > 1 ? "s" : ""} that moved</button>}
              {open[e.code] && (
                <div className="table-wrap" style={{ marginTop: 6 }}>
                  <table>
                    <thead><tr><th>Amount</th><th className="num">{cmp.before_label}</th><th className="num">{cmp.after_label}</th><th className="num">Change</th><th>How it is worked out now</th></tr></thead>
                    <tbody>{e.changes.map((c) => (
                      <tr key={c.code}>
                        <td className="click" onClick={() => onExplain(e.code, c.code)}>{c.label}</td>
                        <td className="num">{c.code === "unpaid_days" ? c.before : inr(c.before)}</td>
                        <td className="num">{c.code === "unpaid_days" ? c.after : inr(c.after)}</td>
                        <td className={`num ${Number(c.delta) > 0 ? "up" : "down"}`}>{c.code === "unpaid_days" ? c.delta : signed(c.delta)}</td>
                        <td className="small mono">{c.formula_after}</td>
                      </tr>))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="muted small">Net pay</div>
              <div className="num">{inr(e.net_before)} → <b>{inr(e.net_after)}</b></div>
              <div className={`small ${Number(e.net_delta) > 0 ? "up" : "down"}`}>{signed(e.net_delta)}</div>
              <button className="small" style={{ marginTop: 6 }} onClick={() => onExplain(e.code, "net_pay")}>Why? Line by line</button>
            </div>
          </div>
        ))}
        {!query && changedAll.length > LIMIT && (
          <div style={{ paddingTop: 10 }}><button className="ghost small" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${changedAll.length}`}</button></div>
        )}
      </div>
      <div className="card">
        <div className="row between">
          <div><b>In plain words</b> <span className="muted small">An AI writes this from the comparison above. It changes nothing, and every number in it is checked against the calculation.</span></div>
          <button disabled={!!busy} onClick={() => explain(false)}>{busy ? "Writing…" : ai ? "Show saved summary" : "Explain the changes"}</button>
        </div>
        {ai && (
          <div style={{ marginTop: 10 }}>
            <Banner kind="ai" title={ai.response.headline} action={<AIChip provider={ai.provider} />}>
              <ul className="drivers">{ai.response.points.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </Banner>
            {ai.validation.map((v, i) => <div key={i} className={`small ${v.level === "ok" ? "up" : "down"}`}>{v.level === "ok" ? "✓ " : "⚠ "}{v.message}</div>)}
            <div className="muted small">Saved as AI answer #{ai.id}{ai.reused_saved_answer ? " (reused, the AI was not asked again)" : ""}. {isModel(ai.provider) && <button className="ghost small" onClick={() => explain(true)}>Ask again</button>}</div>
          </div>
        )}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ 4. register
const REG_COLS = [
  ["unpaid_days", "Unpaid days", false],
  ["fixed_gross", "Monthly salary"],
  ["lop_deduction", "Unpaid leave cut"],
  ["arrears", "Arrears"],
  ["_one_time", "One-time"],
  ["gross", "Gross pay"],
  ["pf_employee", "PF"],
  ["esi_employee", "ESI"],
  ["pt", "Prof. tax"],
  ["tds", "TDS"],
  ["_other_ded", "Other ded."],
  ["net_pay", "Net pay"],
  ["employer_cost", "Cost to co."],
];

function cellValue(e, key) {
  if (key === "_one_time") return Object.entries(e.amounts).filter(([k]) => k.startsWith("one_time.")).reduce((a, [, v]) => a + Number(v), 0);
  if (key === "_other_ded") return Object.entries(e.amounts).filter(([k]) => k.startsWith("deduction.")).reduce((a, [, v]) => a + Number(v), 0);
  return e.amounts[key];
}
function focusFor(e, key) {
  if (key === "_one_time") return Object.keys(e.amounts).find((k) => k.startsWith("one_time.")) || "gross";
  if (key === "_other_ded") return Object.keys(e.amounts).find((k) => k.startsWith("deduction.")) || "total_deductions";
  if (key === "unpaid_days" || key === "fixed_gross") return "lop_deduction";
  return key;
}

function Register({ run, changedCodes, onExplain }) {
  const emps = run.result.employees;
  const totals = run.result.totals;
  const [query, setQuery] = useState("");
  const [show, setShow] = useState("all");
  const [state, setState] = useState("");
  const [sort, setSort] = useState({ key: "", dir: 1 });
  const showOther = emps.some((e) => Number(cellValue(e, "_other_ded")));
  const cols = REG_COLS.filter(([k]) => k !== "_other_ded" || showOther);
  const added = new Set((run.comparison?.employees || []).filter((e) => e.status === "added").map((e) => e.code));
  const tests = {
    all: () => true,
    changed: (e) => changedCodes.has(e.code),
    unpaid: (e) => Number(e.amounts.unpaid_days || 0) > 0,
    arrears: (e) => Number(e.amounts.arrears || 0) !== 0 || Number(cellValue(e, "_one_time")) !== 0,
    new: (e) => added.has(e.code),
  };
  const states = [...new Set(emps.map((e) => e.work_state).filter(Boolean))].sort();
  let rows = emps.filter((e) => tests[show](e) && (!state || e.work_state === state) && matches(query, e.name, e.code));
  if (sort.key) {
    const val = (e) => (sort.key === "_name" ? e.name : Number(cellValue(e, sort.key) || 0));
    rows = [...rows].sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir);
  }
  const sortBy = (key) => setSort((s) => ({ key, dir: s.key === key ? -s.dir : key === "_name" ? 1 : -1 }));
  const arrow = (key) => (sort.key === key ? (sort.dir > 0 ? " ▲" : " ▼") : "");
  const sum = (list, k) => list.reduce((a, e) => a + Number(cellValue(e, k) || 0), 0);
  const filtered = rows.length !== emps.length;
  const options = [["all", "Everyone", emps.length], ["changed", "Paid differently", emps.filter(tests.changed).length],
    ["unpaid", "Unpaid days", emps.filter(tests.unpaid).length], ["arrears", "Arrears or one-time", emps.filter(tests.arrears).length]];
  if (added.size) options.push(["new", "New this month", added.size]);
  return (
    <>
      <SectionTitle n="4" title="Payroll register" />
      <TableToolbar query={query} onQuery={setQuery} shown={rows.length} total={emps.length} noun="employees">
        <Segmented value={show} onChange={setShow} options={options} />
        {states.length > 1 && <Select value={state} onChange={setState} label="State" options={[["", "All states"], ...states.map((x) => [x, stateName(x)])]} />}
      </TableToolbar>
      <div className="table-wrap tall register">
        <table>
          <thead><tr>
            <th className="sortable" onClick={() => sortBy("_name")}>Employee{arrow("_name")}</th>
            {cols.map(([k, l]) => <th key={k} className="num sortable" onClick={() => sortBy(k)}>{l}{arrow(k)}</th>)}
          </tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={cols.length + 1} className="empty">Nobody matches. Clear the search or pick “Everyone”.</td></tr>}
            {rows.map((e) => (
              <tr key={e.code} className={changedCodes.has(e.code) ? "changed" : ""}>
                <td><b>{e.name}</b><div className="muted small">{e.code} · {e.work_state}</div></td>
                {cols.map(([k, , money]) => {
                  const v = cellValue(e, k);
                  const zero = !Number(v);
                  return (
                    <td key={k} className={`num click ${zero ? "muted" : ""}`} onClick={() => onExplain(e.code, focusFor(e, k))} title="Why?">
                      {money === false ? (zero ? "—" : v) : zero ? "—" : inr(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            {filtered && (
              <tr>
                <td>Shown ({rows.length})</td>
                {cols.map(([k, , money]) => <td key={k} className="num">{money === false ? sum(rows, k) : inr(sum(rows, k))}</td>)}
              </tr>
            )}
            <tr className={filtered ? "all" : ""}>
              <td>{filtered ? `All ${emps.length}` : `Total (${emps.length})`}</td>
              {cols.map(([k, , money]) => {
                let v = totals[k];
                if (k === "_one_time") v = Object.entries(totals).filter(([x]) => x.startsWith("one_time.")).reduce((a, [, y]) => a + Number(y), 0);
                if (k === "_other_ded") v = Object.entries(totals).filter(([x]) => x.startsWith("deduction.")).reduce((a, [, y]) => a + Number(y), 0);
                if (k === "unpaid_days") v = emps.reduce((a, e) => a + Number(e.amounts.unpaid_days || 0), 0);
                return <td key={k} className="num">{money === false ? v : inr(v)}</td>;
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="muted small" style={{ marginTop: 6 }}>
        <a href={`/api/runs/${run.id}/export?file=register.csv`}>Download this register (CSV)</a>
      </p>
    </>
  );
}

// ------------------------------------------------------------------ explain drawer
function ExplainDrawer({ run, employee, focus, comparison, onClose }) {
  if (!employee) return null;
  const lines = employee.lines;
  const groups = [
    ["Earnings", lines.filter((l) => l.category === "earning" || ["lop_deduction", "gross"].includes(l.code))],
    ["Deductions", lines.filter((l) => l.category === "deduction" || ["total_deductions", "net_pay"].includes(l.code))],
    ["Paid by the company", lines.filter((l) => l.category === "employer" || l.code === "employer_cost")],
  ];
  return (
    <Drawer title={`${employee.name} · ${run.period_label}`} subtitle={`${employee.code} · net pay ${inr(employee.amounts.net_pay)} · every line below is worked out by the calculation, never by AI`} onClose={onClose}>
      {comparison && comparison.status !== "unchanged" && (
        <Banner kind="info" title={`Why it changed since ${run.comparison.before_label} (${signed(comparison.net_delta)} net):`}>
          <ul className="drivers">{comparison.drivers.map((d, i) => <li key={i}>{d.text}</li>)}</ul>
        </Banner>
      )}
      {employee.issues.map((i, k) => <Banner key={k} kind={i.level === "error" ? "bad" : i.level === "warning" ? "warn" : "info"}>{i.message}</Banner>)}
      {groups.map(([title, ls]) => (
        <div key={title}>
          <div className="group-label">{title}</div>
          {ls.map((l) => <LineItem key={l.code} line={l} focus={focus} change={comparison?.changes.find((c) => c.code === l.code)} before={run.comparison?.before_label} />)}
        </div>
      ))}
    </Drawer>
  );
}

function LineItem({ line, focus, change, before }) {
  const isFocus = line.code === focus || line.children?.some((c) => c.code === focus);
  const [open, setOpen] = useState(isFocus || ["net_pay"].includes(line.code));
  const total = ["gross", "net_pay", "total_deductions", "employer_cost"].includes(line.code);
  // The unpaid leave cut takes money off pay: shown as a negative amount in red, and a bigger cut reads as a loss.
  const cut = line.code === "lop_deduction";
  const money = (amount) => (cut && Number(amount) ? `−${inr(amount)}` : inr(amount));
  return (
    <div className={`line ${isFocus ? "focus" : ""}`} ref={(el) => isFocus && el && el.scrollIntoView({ block: "nearest" })}>
      <div className="lh" onClick={() => setOpen(!open)}>
        <span>{open ? "▾" : "▸"} {total ? <b>{line.label}</b> : line.label}
          {change && <span className={`chip ${(cut ? -1 : 1) * Number(change.delta) > 0 ? "ok" : "bad"}`} style={{ marginLeft: 6 }}>{signed(cut ? -change.delta : change.delta)} vs {before}</span>}
        </span>
        <span className={`num${cut ? " down" : ""}`}>{total ? <b>{inr(line.amount)}</b> : money(line.amount)}</span>
      </div>
      {open && (
        <div className="lb">
          {line.formula && <div className="formula">{line.formula}</div>}
          {line.reason && <p>{line.reason}</p>}
          {change && change.formula_before && <p className="muted small">In {before}: <span className="mono">{change.formula_before}</span></p>}
          {line.rules?.length > 0 && (
            <div className="rules">
              {line.rules.map((r, i) => (
                <span key={i} className={`chip ${r.scope === "override" ? "warn" : r.scope === "statutory" ? "brand" : "info"}`}
                  title={`Version ${r.version_id}`}>
                  {SCOPE_LABEL[r.scope] || r.scope}: {r.title}{r.jurisdiction && r.jurisdiction !== "IN" ? ` (${r.jurisdiction})` : ""}{r.effective_from ? ` · from ${day(r.effective_from)}` : ""}
                </span>
              ))}
            </div>
          )}
          {line.children?.length > 0 && (
            <div className="children">
              {line.children.map((c) => (
                <div key={c.code} className={c.code === focus ? "line focus" : ""} style={c.code === focus ? { padding: 6 } : {}}>
                  <div className="crow"><span>{c.label}</span><span className={`num${cut ? " down" : ""}`}>{money(c.amount)}</span></div>
                  {c.formula && <div className="small mono muted">{c.formula}</div>}
                  {c.reason && <div className="small muted">{c.reason}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 5. approval
function ApprovalSection({ run, busy, onAction, isChecker, isMaker }) {
  const [note, setNote] = useState("");
  const t = run.result?.totals || {};
  const same = run.submitted_result_hash && run.submitted_result_hash === run.result_hash;
  return (
    <>
      <SectionTitle n="5" title="Approval" />
      <div className="card">
        <div className="grid2">
          <div>
            <h3>What is being approved</h3>
            {run.result ? (
              <dl className="kv">
                <dt>Employees</dt><dd>{run.result.employee_count}</dd>
                <dt>Gross pay</dt><dd>{inr(t.gross)}</dd>
                <dt>Deductions</dt><dd>{inr(t.total_deductions)} (PF {inr(t.pf_employee)}, ESI {inr(t.esi_employee)}, PT {inr(t.pt)}, TDS {inr(t.tds)})</dd>
                <dt>Net pay</dt><dd><b>{inr(t.net_pay)}</b></dd>
                <dt>Company PF + ESI</dt><dd>{inr(Number(t.pf_employer || 0) + Number(t.esi_employer || 0))}</dd>
                <dt>Result fingerprint</dt><dd><span className="hash">{run.result_hash.slice(0, 16)}</span></dd>
              </dl>
            ) : <p className="muted">Nothing calculated yet.</p>}
          </div>
          <div>
            <h3>Finance team employee and finance head</h3>
            <dl className="kv">
              <dt>Prepared by</dt><dd>{run.calculated_by?.name || "—"} {run.calculation_count > 1 && <span className="muted small">({run.calculation_count} calculations)</span>}</dd>
              <dt>Submitted</dt><dd>{run.submitted_by ? `${run.submitted_by.name}, ${when(run.submitted_at)}` : "—"}</dd>
              <dt>Decision</dt><dd>{run.decided_by ? `${run.status === "rejected" ? "Sent back" : "Approved"} by ${run.decided_by.name}, ${when(run.decided_at)}` : "—"}</dd>
              {run.submitted_result_hash && <><dt>Unchanged since submitted</dt><dd>{same ? <span className="chip ok">✓ same result the finance team employee submitted</span> : <span className="chip bad">result differs from submission</span>}</dd></>}
              {run.freshness && <><dt>Inputs still current</dt><dd>{run.freshness.fresh ? <span className="chip ok">✓ no data, change or rule moved</span> : <span className="chip warn">{run.freshness.reasons.join("; ")}</span>}</dd></>}
            </dl>
          </div>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          {isMaker && run.can.calculate && <button disabled={!!busy} onClick={() => onAction("calculate")}>{run.result ? "Calculate again" : "Calculate"}</button>}
          {isMaker && run.can.submit && <button className="primary" disabled={!!busy || (run.freshness && !run.freshness.fresh)} onClick={() => onAction("submit", {}, null)}>Submit for approval</button>}
          {isMaker && run.can.cancel && <button className="ghost" disabled={!!busy} onClick={() => window.confirm("Cancel this month? You can open it again later.") && onAction("cancel")}>Cancel month</button>}
          {isChecker && run.status === "awaiting_approval" && (
            <>
              <input style={{ flex: 1, minWidth: 220 }} placeholder="Note (required to send back)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="danger" disabled={!!busy || !run.can.reject || !note.trim()} onClick={() => onAction("reject", { note })}>Send back</button>
              <button className="good" disabled={!!busy || !run.can.approve} onClick={() => onAction("approve", { note })}>Approve {run.period_label}</button>
            </>
          )}
        </div>
        {run.status === "awaiting_approval" && !isChecker && <p className="muted small" style={{ marginTop: 8 }}>Sign in as the finance head (e.g. <b>finance head</b>) to approve.</p>}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ 6. files
function FilesSection({ run, busy, onAction }) {
  const files = run.files?.files;
  return (
    <>
      <SectionTitle n="6" title="Register and payment files" />
      <div className="card">
        {!files && run.status === "approved" ? (
          <>
            <p className="muted">Approved, but the files have not been written yet{run.last_error ? " (see the message at the top)" : ""}. The approval stands; finalising again does not change any number.</p>
            {run.can.finalize && <button className="primary" disabled={!!busy} onClick={() => onAction("finalize")}>Finalise again</button>}
          </>
        ) : !files ? (
          <p className="muted">Written when the finance head approves. Until then you can download a draft register from the table above.</p>
        ) : (
          <div className="grid2">
            <div>
              <h3>Files</h3>
              <div className="table-wrap"><table>
                <thead><tr><th>File</th><th className="num">Rows</th></tr></thead>
                <tbody>{Object.entries(files).map(([name, f]) => (
                  <tr key={name}><td><a href={`/api/runs/${run.id}/files/${name}`}>{name}</a></td><td className="num">{f.rows}</td></tr>
                ))}</tbody>
              </table></div>
              <p className="muted small" style={{ marginTop: 6 }}>One row per employee. No government IDs; no payment is made.</p>
            </div>
            <div>
              <h3>Files match the register</h3>
              {run.files.reconciliation.map((c, i) => (
                <div key={i} className="check"><span className={`dot ${c.matches ? "ok" : "error"}`} /><span>{c.check}: {c.file_total} {c.matches ? "=" : "≠"} {c.register_total}</span></div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function DuesSection({ run, me, onChange }) {
  const total = run.dues.reduce((sum, x) => sum + Number(x.total), 0);
  return (
    <>
      <SectionTitle n="7" title="What to pay the government" right={<a href="#/dues" className="small">All dues</a>} />
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>{inr(total)} for {run.period_label}: the deductions above plus the company's own PF and ESI. Record each payment with its challan number.</p>
        <DuesTable dues={run.dues} canRecord={me.roles.includes("maker") || me.roles.includes("checker")} onChange={onChange} showMonth={false} />
      </div>
    </>
  );
}

// ------------------------------------------------------------------ what-if
const WHAT_IF = [
  ["lop", "divisor", "One day of unpaid leave is", [["days_in_month", "pay ÷ days in the month"], [30, "pay ÷ 30"], [26, "pay ÷ 26 working days"]]],
  ["pf_policy", "wage_base", "PF is worked out on", [["capped", "the first ₹15,000 of basic"], ["full", "all of basic"]]],
  ["rounding", "earnings", "Rounding", [["per_component", "each part of pay"], ["gross_only", "only the total"]]],
  ["pf_policy", "arrears_contribution", "PF on arrears", [["recompute_delta", "yes, on the revised months"], ["none", "no"]]],
  ["esi_policy", "eligibility_wage", "ESI eligibility uses", [["gross_this_month", "gross this month"], ["regular_earned", "regular pay only"], ["contracted_monthly", "full monthly salary"]]],
];

function WhatIf({ run, me }) {
  const [values, setValues] = useState({});
  const [busy, act] = useAction();
  if (!me.roles.includes("maker")) return null;
  const create = async () => {
    const overrides = {};
    for (const [key, field] of WHAT_IF) {
      const v = values[`${key}.${field}`];
      if (v === undefined || v === "") continue;
      overrides[key] = { ...(overrides[key] || {}), [field]: /^\d+$/.test(v) ? Number(v) : v };
    }
    const base = run.run_type === "draft" ? run.base_run.id : run.id;
    const draft = await act("draft", () => api.post(`/runs/${base}/draft-rerun`, { overrides }), "Draft re-run calculated");
    if (draft) go(`/runs/${draft.id}`);
  };
  return (
    <>
      <SectionTitle title="What if? (draft re-run)" />
      <div className="card">
        <div className="grid2">
          {WHAT_IF.map(([key, field, label, options]) => (
            <Field key={`${key}.${field}`} label={label}>
              <select value={values[`${key}.${field}`] ?? ""} onChange={(e) => setValues({ ...values, [`${key}.${field}`]: e.target.value })}>
                <option value="">Keep the current setting</option>
                {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
          ))}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={!!busy} onClick={create}>Run a draft and compare</button>
          {run.run_type === "draft" && <span className="muted small">This creates another draft of #{run.base_run.id}.</span>}
        </div>
      </div>
    </>
  );
}

function Repeatable({ run }) {
  const [out, setOut] = useState(null);
  const [busy, act] = useAction();
  const verify = async () => setOut(await act("verify", () => api.post(`/runs/${run.id}/verify`)));
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row between">
        <div><b>Same inputs, same result</b> <span className="muted small">Calculate again from the stored inputs (no database, no AI) and compare fingerprints.</span></div>
        <button disabled={!!busy} onClick={verify}>Run again and compare</button>
      </div>
      {out && (
        <div style={{ marginTop: 8 }}>
          {out.identical ? <Banner kind="ok">Identical. Stored <span className="hash">{out.stored_result_hash.slice(0, 16)}</span> = recalculated <span className="hash">{out.recalculated_hash.slice(0, 16)}</span></Banner>
            : <Banner kind="bad">Different! Stored {out.stored_result_hash.slice(0, 16)} vs {out.recalculated_hash.slice(0, 16)}</Banner>}
        </div>
      )}
    </div>
  );
}

function Events({ run }) {
  const [open, setOpen] = useState(false);
  const events = useMemo(() => [...run.events].reverse(), [run.events]);
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row between"><b>Audit trail ({run.events.length})</b><button className="ghost small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button></div>
      {open && (
        <div className="table-wrap" style={{ marginTop: 8 }}><table>
          <thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead>
          <tbody>{events.map((e, i) => <tr key={i}><td className="small">{when(e.at)}</td><td className="small">{e.actor}</td><td>{e.message}</td></tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}
