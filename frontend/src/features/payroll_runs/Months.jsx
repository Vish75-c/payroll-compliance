import React, { useState } from "react";
import { api } from "../../shared/api.js";
import { PageHelp } from "../../shared/Help.jsx";
import { go } from "../../shared/routing.js";
import { Banner, Field, Loading, StatusChip, cycleHint, inr, useAction, useLoad, when } from "../../shared/ui.jsx";

export default function Months({ company, me }) {
  const runs = useLoad(() => api.get(`/companies/${company.id}/runs`), [company.id]);
  const snaps = useLoad(() => api.get(`/companies/${company.id}/snapshots`), [company.id]);
  const [picked, setPicked] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [days, setDays] = useState({});  // month -> day count typed by the finance team employee
  const [busy, act] = useAction();
  const isMaker = me.roles.includes("maker");
  const approved = (snaps.data || []).filter((s) => s.status === "approved");
  // Suggest the month after the latest regular month (or the current month when there is none yet).
  const latest = (runs.data || []).filter((r) => r.run_type !== "draft" && r.status !== "cancelled").map((r) => r.period).sort().pop();
  const nextMonth = (p) => { const [y, m] = p.split("-").map(Number); return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`; };
  const period = picked || (latest ? nextMonth(latest) : new Date().toISOString().slice(0, 7));
  const setPeriod = setPicked;
  const preset = useLoad(() => (/^\d{4}-\d{2}$/.test(period) ? api.get(`/companies/${company.id}/day-count?period=${period}`) : Promise.resolve(null)), [company.id, period]);
  const dayCount = days[period] ?? (preset.data ? String(preset.data.days) : "");
  const open = async () => {
    const body = { period, day_count: dayCount };
    if (snapshotId) body.snapshot_id = Number(snapshotId);
    if (knowledge) body.knowledge_date = knowledge;
    const run = await act("open", () => api.post(`/companies/${company.id}/runs`, body), `${period} opened`);
    if (run) go(`/runs/${run.id}`);
  };
  return (
    <div>
      <div className="topbar"><div className="title-row"><h1>Payroll months</h1><PageHelp topic="months" /></div></div>
      {isMaker && (
        <div className="card">
          <h2>Open a month</h2>
          {approved.length === 0 && <Banner kind="warn">First get HR data and mark it as reviewed on the <a href="#/data">HR data</a> page.</Banner>}
          <div className="row" style={{ alignItems: "flex-end" }}>
            <Field label="Month" note={cycleHint(preset.data?.cycle)}><input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
            <Field label="Days for one day's pay">
              <input type="number" min="1" max="31" style={{ width: 90 }} value={dayCount} onChange={(e) => setDays({ ...days, [period]: e.target.value })} />
            </Field>
            <Field label="HR data to use">
              <select value={snapshotId} onChange={(e) => setSnapshotId(e.target.value)}>
                <option value="">Latest reviewed{approved[0] ? ` (#${approved[0].id} ${approved[0].source_system})` : ""}</option>
                {approved.map((s) => <option key={s.id} value={s.id}>#{s.id} {s.source_system} · {when(s.created_at)}</option>)}
              </select>
            </Field>
            {advanced && (
              <Field label="Salary changes known as of" hint="Leave empty for today. Used to re-create past months as they were known.">
                <input type="date" value={knowledge} onChange={(e) => setKnowledge(e.target.value)} />
              </Field>
            )}
            <button className="primary" disabled={!!busy || !approved.length || !period || !dayCount} onClick={open}>Open {period}</button>
            <button className="ghost small" onClick={() => setAdvanced(!advanced)}>{advanced ? "Fewer options" : "More options"}</button>
          </div>
        </div>
      )}
      <div className="card">
        <h2>All runs</h2>
        {!runs.data ? <Loading error={runs.error} /> : runs.data.length === 0 ? <div className="empty">No months yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Month</th><th>Run</th><th>Status</th><th className="num">Employees</th><th className="num">Gross</th><th className="num">Net pay</th><th className="num">Cost to company</th><th>Calculated</th><th></th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id}>
                <td><b>{r.label}</b></td>
                <td>#{r.id} {r.run_type === "draft" ? <span className="chip">draft re-run of #{r.base_run_id}</span> : <span className="chip brand">regular</span>}</td>
                <td><StatusChip status={r.status} label={r.status_label} /></td>
                <td className="num">{r.employee_count ?? "—"}</td><td className="num">{inr(r.gross)}</td><td className="num">{inr(r.net_pay)}</td><td className="num">{inr(r.employer_cost)}</td>
                <td className="small">{when(r.calculated_at)}</td>
                <td><button className="small" onClick={() => go(`/runs/${r.id}`)}>Open</button></td>
              </tr>))}
            </tbody></table></div>
        )}
      </div>
    </div>
  );
}
