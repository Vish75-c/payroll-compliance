import React, { useState } from "react";
import { api } from "../../shared/api.js";
import { AIChip, DataView, Json, Loading, StatusChip, useLoad, when } from "../../shared/ui.jsx";

export default function AILog({ company }) {
  const { data, error } = useLoad(() => api.get(`/companies/${company.id}/ai-log`), [company.id]);
  const [open, setOpen] = useState(null);
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <div className="topbar"><div><h1>AI log</h1></div></div>
      <div className="card">
        <h2>Answers ({data.items.length})</h2>
        {data.items.length === 0 ? <div className="empty">No AI answers yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>When</th><th>Task</th><th>By</th><th>Status</th><th>Checks</th><th className="num">Reused</th><th></th></tr></thead>
            <tbody>{data.items.map((i) => (
              <React.Fragment key={i.id}>
                <tr>
                  <td className="small">{when(i.created_at)}</td>
                  <td>{i.purpose_label}</td>
                  <td><AIChip provider={i.provider} /></td>
                  <td><StatusChip status={i.status} label={i.status_label} />{i.reviewed_by && <div className="muted small">{i.reviewed_by.name}</div>}</td>
                  <td className="small">{(i.validation || []).map((v, k) => <div key={k} className={v.level === "ok" ? "up" : v.level === "info" ? "muted" : "down"}>{v.message}</div>)}</td>
                  <td className="num">{i.reuse_count}</td>
                  <td><button className="ghost small" onClick={() => setOpen(open === i.id ? null : i.id)}>{open === i.id ? "Hide" : "Show"}</button></td>
                </tr>
                {open === i.id && <tr><td colSpan={7}><Exchange item={i} /></td></tr>}
              </React.Fragment>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}

// One question and answer, in words and tables. The exact text sent and received stays one click away
// for anyone auditing the AI.
function Exchange({ item }) {
  const [raw, setRaw] = useState(false);
  let asked = item.request.prompt;
  try { asked = JSON.parse(item.request.prompt); } catch { /* plain text */ }
  return (
    <div>
      {raw ? (
        <div className="grid2">
          <div><h3>Sent</h3><pre className="json">{item.request.system}{"\n\n"}{item.request.prompt}</pre></div>
          <div><h3>Received</h3><Json value={item.response} /></div>
        </div>
      ) : (
        <div className="grid2">
          <div><h3>Asked</h3><p className="small">{item.request.system}</p>{typeof asked === "string" ? <p className="small">{asked}</p> : <DataView value={asked} />}</div>
          <div><h3>Answer</h3><DataView value={item.response} /></div>
        </div>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <span className="muted small">input fingerprint {item.input_hash.slice(0, 16)} · {item.latency_ms} ms</span>
        <span className="spacer" />
        <button className="ghost small" onClick={() => setRaw(!raw)}>{raw ? "Show as text" : "Show the exact text sent and received"}</button>
      </div>
    </div>
  );
}
