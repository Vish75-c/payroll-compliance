import React, { useState } from "react";
import { api } from "../../shared/api.js";
import { go } from "../../shared/routing.js";
import { Banner, Field, Loading, Segmented, Stat, day, inr, useAction, useLoad } from "../../shared/ui.jsx";

const STATUS_CHIP = { due: "info", due_soon: "warn", overdue: "bad", paid: "ok", paid_late: "warn" };
const WHERE_TO_PAY = {
  tds: "Challan ITNS 281 on the income tax portal, with the company's TAN",
  pf: "ECR on the EPFO employer portal; keep the TRRN",
  esi: "Monthly contribution on the ESIC portal; keep the challan number",
  pt: "State professional tax portal; keep the receipt number",
};

export function DueStatus({ status }) {
  return <span className={`chip ${STATUS_CHIP[status.code] || ""}`}>{status.label}</span>;
}

// Record a payment: the date it was made and the challan / TRRN / CRN number.
function PayForm({ due, onDone, onCancel }) {
  const [paidOn, setPaidOn] = useState(new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");
  const [busy, act] = useAction();
  const save = async () => {
    const ok = await act("pay", () => api.post(`/dues/${due.id}/paid`, { paid_on: paidOn, reference }), `${due.title} for ${due.period_label} recorded as paid`);
    if (ok) onDone();
  };
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <Field label="Paid on"><input type="date" value={paidOn} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setPaidOn(e.target.value)} /></Field>
      <Field label={due.kind === "pf" ? "TRRN" : due.kind === "tds" ? "Challan (CIN / BSR + serial)" : "Challan / receipt number"}>
        <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="as on the receipt" />
      </Field>
      <button className="primary" disabled={!!busy || !reference.trim()} onClick={save}>Save</button>
      <button className="ghost" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function UndoForm({ due, onDone, onCancel }) {
  const [reason, setReason] = useState("");
  const [busy, act] = useAction();
  const save = async () => {
    const ok = await act("undo", () => api.post(`/dues/${due.id}/undo`, { reason }), "Payment record undone");
    if (ok) onDone();
  };
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <Field label="Why is the record being undone?"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. wrong challan number entered" /></Field>
      <button disabled={!!busy || !reason.trim()} onClick={save}>Undo</button>
      <button className="ghost" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export function DuesTable({ dues, canRecord, onChange, showMonth = true }) {
  const [editing, setEditing] = useState(null); // [id, "pay" | "undo"]
  const done = () => { setEditing(null); onChange(); };
  const byMonth = [];
  for (const x of dues) {
    const last = byMonth[byMonth.length - 1];
    if (last && last.period === x.period) last.items.push(x); else byMonth.push({ period: x.period, label: x.period_label, run_id: x.run_id, items: [x] });
  }
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>What</th><th className="num">People</th><th className="num">Employees' share</th><th className="num">Company's share</th>
        <th className="num">To pay</th><th>Due by</th><th>Status</th><th>Payment</th></tr></thead>
      <tbody>{byMonth.map((g) => (
        <React.Fragment key={g.period}>
          {showMonth && (
            <tr className="group-row"><td colSpan={8}>
              <b>{g.label}</b> <span className="muted small">· {inr(g.items.reduce((s, x) => s + Number(x.total), 0))} in all ·</span>{" "}
              <a className="small" href={`#/runs/${g.run_id}`}>open the month</a>
            </td></tr>
          )}
          {g.items.map((x) => (
            <React.Fragment key={x.id}>
              <tr>
                <td><b>{x.title}</b><div className="muted small">{WHERE_TO_PAY[x.kind]}</div></td>
                <td className="num">{x.people}</td>
                <td className="num">{inr(x.employee_amount)}</td>
                <td className="num">{Number(x.employer_amount) ? inr(x.employer_amount) : "—"}</td>
                <td className="num"><b>{inr(x.total)}</b></td>
                <td>{day(x.due_date)}</td>
                <td><DueStatus status={x.status} /></td>
                <td className="small">
                  {x.paid_on ? (
                    <>
                      {day(x.paid_on)} · {x.reference}
                      <div className="muted">by {x.paid_by?.name || "—"}</div>
                      {canRecord && <button className="ghost small" style={{ paddingLeft: 0 }} onClick={() => setEditing([x.id, "undo"])}>Undo</button>}
                    </>
                  ) : canRecord ? <button className="small" onClick={() => setEditing([x.id, "pay"])}>Mark as paid</button> : <span className="muted">not paid yet</span>}
                </td>
              </tr>
              {editing && editing[0] === x.id && (
                <tr><td colSpan={8} style={{ background: "var(--line-2)" }}>
                  {editing[1] === "pay" ? <PayForm due={x} onDone={done} onCancel={() => setEditing(null)} />
                    : <UndoForm due={x} onDone={done} onCancel={() => setEditing(null)} />}
                </td></tr>
              )}
            </React.Fragment>
          ))}
        </React.Fragment>
      ))}</tbody>
    </table></div>
  );
}

export default function Dues({ company, me }) {
  const [filter, setFilter] = useState("open");
  const { data, error, reload } = useLoad(() => api.get(`/companies/${company.id}/dues?status=${filter}`), [company.id, filter]);
  if (!data) return <Loading error={error} />;
  const s = data.summary;
  const c = data.company;
  const canRecord = me.roles.includes("maker") || me.roles.includes("checker");
  return (
    <div>
      <div className="topbar">
        <div>
          <div className="title-row"><h1>Government dues</h1></div>
        </div>
      </div>
      {s.overdue > 0 && (
        <Banner kind="bad" title={`${s.overdue} payment${s.overdue > 1 ? "s are" : " is"} past the due date.`}>
          Late deposits attract interest and penalties (for example 1.5% a month on TDS, 12% a year on PF). Pay them and record the challan here.
        </Banner>
      )}
      <div className="grid4" style={{ marginBottom: 16 }}>
        <Stat label="Still to pay" value={s.open} />
        <Stat label="Overdue" value={s.overdue} />
        <Stat label="Amount still to pay" value={inr(s.open_total)} />
        <Stat label="Paid and recorded" value={s.paid} />
      </div>
      <div className="card">
        <div className="card-head">
          <h2>Payments</h2>
          <Segmented value={filter} onChange={setFilter} options={[["open", "Still to pay"], ["paid", "Paid"], ["all", "All"]]} />
        </div>
        {data.dues.length === 0 ? (
          <div className="empty">{filter === "open" ? "Nothing to pay." : "None."}</div>
        ) : <DuesTable dues={data.dues} canRecord={canRecord} onChange={reload} />}
      </div>
    </div>
  );
}
