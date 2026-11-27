import React, { useEffect, useState } from "react";
import { api } from "../../shared/api.js";
import { go } from "../../shared/routing.js";
import { Banner, Bars, Field, Loading, StatusChip, day, inr, signed, stateName, useAction, useLoad, when } from "../../shared/ui.jsx";
import { DueStatus } from "../dues/Dues.jsx";

const TONE = { finalized: "ok", approved: "ok", awaiting_approval: "warn", rejected: "bad", calculated: "info", draft: "info" };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function Icon({ name }) {
  const paths = {
    people: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8 1a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM2.5 19c.6-3 3-4.6 5.5-4.6s4.9 1.6 5.5 4.6M14 14.6c2.3-.4 5 .8 5.6 4.4",
    wallet: "M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5V17a2 2 0 0 0 2 2h13a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H6.5A2.5 2.5 0 0 1 4 7.5Zm12 6h.01",
    check: "M5 12.5l4 4L19 7",
    bank: "M4 10h16M6 10v7m4-7v7m4-7v7m4-7v7M3 20h18M12 4l8 4H4l8-4Z",
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function Kpi({ icon, label, value, hint, tone, onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className={`kpi${tone ? ` kpi-${tone}` : ""}${onClick ? " kpi-link" : ""}`} onClick={onClick} type={onClick ? "button" : undefined}>
      <span className="kpi-icon"><Icon name={icon} /></span>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </Tag>
  );
}

/** Payout details for one month: gross pay split into net pay and deductions (PF, ESI, PT, TDS taken from salaries). */
function PayoutDonut({ month: m }) {
  const [hover, setHover] = useState(null);
  if (!m?.regular?.net_pay || !m.regular.gross) {
    return <div className="card mv-card"><h2>Payout details</h2><div className="empty">Calculated once the month is worked out.</div></div>;
  }
  const gross = Number(m.regular.gross);
  const net = Number(m.regular.net_pay);
  const parts = [
    { key: "net", label: "Net pay", value: net, colour: "#0f7a35" },
    { key: "ded", label: "Deductions", value: Math.max(0, gross - net), colour: "#c4913a" },
  ];
  const R = 64, C = 2 * Math.PI * R, GAP = 2;
  let start = 0;
  const arcs = parts.map((x) => {
    const len = gross ? (x.value / gross) * C : 0;
    const arc = { ...x, len: Math.max(0, len - GAP), offset: -start };
    start += len;
    return arc;
  });
  const shown = parts.find((x) => x.key === hover);
  return (
    <div className="card mv-card">
      <div className="card-head"><h2>Payout details</h2></div>
      <div className="payout">
        <svg className="payout-ring" viewBox="0 0 160 160" role="img"
          aria-label={`${m.label}: gross pay ${inr(gross)}, net pay ${inr(net)}, deductions ${inr(gross - net)}`}>
          <circle cx="80" cy="80" r={R} className="payout-track" />
          {arcs.map((x, i) => (
            <circle key={x.key} cx="80" cy="80" r={R} className={`payout-arc${hover && hover !== x.key ? " is-dim" : ""}`}
              stroke={x.colour} strokeDasharray={`${x.len} ${C}`} strokeDashoffset={x.offset} style={{ "--len": x.len, "--i": i }}
              tabIndex={0} onMouseEnter={() => setHover(x.key)} onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(x.key)} onBlur={() => setHover(null)} aria-label={`${x.label}: ${inr(x.value)}`} />
          ))}
          <text x="80" y="74" className="payout-c-label">{shown ? shown.label : "Net pay"}</text>
          <text x="80" y="94" className="payout-c-value">{inr(shown ? shown.value : net)}</text>
          {shown && <text x="80" y="111" className="payout-c-share">{Math.round((shown.value / gross) * 100)}% of gross</text>}
        </svg>
        <ul className="payout-legend">
          {parts.map((x) => (
            <li key={x.key} onMouseEnter={() => setHover(x.key)} onMouseLeave={() => setHover(null)}>
              <span className="payout-swatch" style={{ background: x.colour }} aria-hidden="true" />
              <span><small>{x.label}</small><b>{inr(x.value)}</b></span>
            </li>
          ))}
          <li className="payout-total"><span className="payout-swatch payout-swatch-total" aria-hidden="true" /><span><small>Gross pay</small><b>{inr(gross)}</b></span></li>
        </ul>
      </div>
    </div>
  );
}

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fyOf = (period) => { const [y, m] = period.split("-").map(Number); return m >= 4 ? y : y - 1; };
const fyMonths = (fy) => Array.from({ length: 12 }, (_, i) => { const m = ((3 + i) % 12) + 1; const y = m >= 4 ? fy : fy + 1; return `${y}-${String(m).padStart(2, "0")}`; });

/** The month strip, then the selected month's payout, people and progress (a payroll month at a glance). */
function MonthView({ data, canOpen, opening, setOpening, openForm }) {
  const byPeriod = Object.fromEntries(data.months.map((m) => [m.period, m]));
  const first = data.months.find((m) => m.regular)?.period || data.next_period || new Date().toISOString().slice(0, 7);
  const [selected, setSelected] = useState(first);
  const [fy, setFy] = useState(fyOf(first));
  const pick = (period) => { setSelected(period); setOpening(false); };
  // "Open the next month" from the banner jumps the strip to that month, where the form appears.
  useEffect(() => {
    if (opening && data.next_period && selected !== data.next_period) { setSelected(data.next_period); setFy(fyOf(data.next_period)); }
  }, [opening]);
  const month = byPeriod[selected];
  const run = month?.regular;
  const facts = run?.facts;
  const [y, mo] = selected.split("-").map(Number);
  const label = `${["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][mo - 1]} ${y}`;
  const older = data.months.filter((m) => m.period < selected && m.regular?.employee_count != null)[0];
  const peopleDelta = run?.employee_count != null && older ? run.employee_count - older.regular.employee_count : null;
  return (
    <>
      <div className="mv-strip" role="tablist" aria-label="Payroll months">
        <button type="button" className="mv-arrow" aria-label="Previous financial year" onClick={() => setFy(fy - 1)}>‹</button>
        <div className="mv-months">
          {fyMonths(fy).map((period) => {
            const m = byPeriod[period];
            const tone = m?.regular ? (TONE[m.regular.status] || "info") : period === data.next_period ? "next" : "none";
            const [py, pm] = period.split("-").map(Number);
            return (
              <button key={period} type="button" role="tab" aria-selected={period === selected} className={`mv-month mv-${tone}${period === selected ? " is-on" : ""}`}
                onClick={() => pick(period)} title={m?.regular ? m.regular.status_label : period === data.next_period ? "Next month to open" : "Not started"}>
                <b>{MONTHS_SHORT[pm - 1]}</b><small>{py}</small><i aria-hidden="true" />
              </button>
            );
          })}
        </div>
        <button type="button" className="mv-arrow" aria-label="Next financial year" onClick={() => setFy(fy + 1)}>›</button>
      </div>

      <div className="mv-title">
        <h2>{label}</h2>
        <span className="mv-period">Pay period {day(`${selected}-01`)} to {day(facts?.pay_period?.to || `${selected}-${new Date(y, mo, 0).getDate()}`)}</span>
        {run && <StatusChip status={run.status} label={run.status_label} />}
        <span className="spacer" />
        {run && <button className="primary" onClick={() => go(`/runs/${run.id}`)}>Open {label} →</button>}
        {!run && selected === data.next_period && canOpen && !opening && <button className="primary" onClick={() => setOpening(true)}>Open {label}</button>}
      </div>
      {opening && selected === data.next_period && openForm}
      {!run && selected === data.next_period && data.next_blocked && (
        <div className="mv-blocked" role="status"><span aria-hidden="true">🔒</span><span>{data.next_blocked}</span></div>
      )}

      {run ? (
        <div className="mv-grid">
          <PayoutDonut month={month} />
          <div className="card mv-card">
            <div className="card-head"><h2>Employee details</h2></div>
            <div className="mv-people">
              <b>{run.employee_count ?? "—"}</b><span>employees paid</span>
              {peopleDelta !== null && <small className={peopleDelta > 0 ? "up" : peopleDelta < 0 ? "down" : ""}>{peopleDelta === 0 ? "same as" : `${signed(peopleDelta)} vs`} {older.label}</small>}
            </div>
            <ul className="mv-facts">
              <li className="mv-f-green"><b>{facts.joined}</b><span>Joined</span></li>
              <li className="mv-f-gold"><b>{facts.left}</b><span>Left</span></li>
              <li className="mv-f-slate"><b>{facts.with_unpaid_days}</b><span>With unpaid days</span></li>
              <li className="mv-f-teal"><b>{facts.changes}</b><span>Changes</span></li>
            </ul>
            <div className="mv-days"><b>{facts.day_count.days}</b><span>days for one day's pay · {facts.day_count.source}</span></div>
          </div>
        </div>
      ) : (
        <div className="card mv-empty">
          <b>{selected === data.next_period ? `${label} is the next month to run.` : `${label} has not been run.`}</b>
          <span className="muted">{selected === data.next_period ? "Open it to read the HR data and work out the month." : "Pick a month with a coloured dot to see its payout."}</span>
        </div>
      )}
    </>
  );
}

/** The finance head's to-do list: approvals and government payments that are waiting on them. */
function TodoPanel({ data }) {
  const items = [];
  for (const r of data.awaiting_approval) {
    items.push({ key: `a${r.id}`, icon: "✓", tone: "warn", title: <><b>{r.label}</b> is waiting for your approval.</>,
      note: `Net pay ${inr(r.net_pay)} for ${r.employee_count} employees`, action: "Approve", run: () => go(`/runs/${r.id}`) });
  }
  for (const r of data.approved_waiting || []) {
    items.push({ key: `f${r.id}`, icon: "⟳", tone: "info", title: <><b>{r.label}</b> is approved but its files were not written.</>,
      note: "Finalising again does not change any number.", action: "Finalise again", run: () => go(`/runs/${r.id}`) });
  }
  if (data.dues?.overdue) {
    items.push({ key: "overdue", icon: "!", tone: "bad", title: <><b>{data.dues.overdue} government payment{data.dues.overdue > 1 ? "s" : ""}</b> overdue.</>,
      note: `${inr(data.dues.overdue_total)} to deposit, with interest growing`, action: "Pay dues", run: () => go("/dues") });
  }
  if (data.dues?.due_soon) {
    items.push({ key: "soon", icon: "⌛", tone: "info", title: <><b>{data.dues.due_soon} payment{data.dues.due_soon > 1 ? "s" : ""}</b> due in the next 7 days.</>,
      note: `${inr(data.dues.due_soon_total)} in all`, action: "View", run: () => go("/dues") });
  }
  return (
    <aside className="todo" aria-labelledby="todo-title">
      <div className="todo-head"><h2 id="todo-title">To do</h2>{items.length > 0 && <span className="todo-count">{items.length}</span>}</div>
      {items.length === 0 ? (
        <div className="todo-empty"><span aria-hidden="true">✓</span><b>You're all caught up</b></div>
      ) : (
        <ul>
          {items.map((t, i) => (
            <li key={t.key} className={`todo-item todo-${t.tone}`} style={{ "--i": i }}>
              <span className="todo-icon" aria-hidden="true">{t.icon}</span>
              <div>
                <p>{t.title}</p>
                {t.note && <small>{t.note}</small>}
                <button className="todo-btn" onClick={t.run}>{t.action}</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

export default function Overview({ company, me }) {
  const { data, error, reload } = useLoad(() => api.get(`/companies/${company.id}/overview`), [company.id]);
  const [busy, act] = useAction();
  const [opening, setOpening] = useState(false);
  const [days, setDays] = useState("");
  if (!data) return <Loading error={error} />;
  const isMaker = me.roles.includes("maker");
  const isChecker = me.roles.includes("checker");
  const dayCount = days || (data.next_day_count ? String(data.next_day_count.days) : "");
  const open = async (period) => {
    const run = await act("open", () => api.post(`/companies/${company.id}/runs`, { period, day_count: dayCount }), `${period} opened`);
    if (run) go(`/runs/${run.id}`);
  };
  const finals = data.months.filter((m) => m.regular?.status === "finalized");
  const latest = data.months.find((m) => m.regular?.net_pay);
  const before = latest && data.months.slice(data.months.indexOf(latest) + 1).find((m) => m.regular?.net_pay);
  const latestDelta = latest && before ? Number(latest.regular.net_pay) - Number(before.regular.net_pay) : null;
  // A month opens only once every earlier month is final (the server says why not in next_blocked).
  const canOpenNext = isMaker && data.next_period && !data.next_blocked && !data.months.some((m) => m.period === data.next_period && m.regular);
  const waiting = data.awaiting_approval[0];
  const firstName = (me.user.name || "").split(" ")[0];

  // One clear next step, depending on who is signed in and where the company is.
  let next = null;
  if (isMaker && data.setting_up) next = { label: "Continue setup", run: () => go(`/onboarding/${data.company.id}`) };
  else if (isChecker && waiting) next = { label: `Review ${waiting.label}`, run: () => go(`/runs/${waiting.id}`) };
  else if (isMaker && !data.approved_snapshot) next = { label: "Get HR data", run: () => go("/data") };
  else if (canOpenNext) next = { label: `Open ${data.next_period}`, run: () => setOpening(true) };
  else if (latest) next = { label: `Go to ${latest.label}`, run: () => go(`/runs/${latest.regular.id}`) };

  return (
    <div className={`ov${isChecker ? " dash-layout" : ""}`}>
    <div className="dash">
      <section className="dash-hero">
        <div className="dash-rings" aria-hidden="true"><i /><i /><i /><b /></div>
        <div>
          <p className="dash-hello">{greeting()}{firstName ? `, ${firstName}` : ""}</p>
          <h1>{data.company.name}</h1>
        </div>
        {next && <button className="primary lg dash-cta" onClick={next.run}>{next.label} <span aria-hidden="true">→</span></button>}
      </section>

      {!isChecker && data.dues && data.dues.overdue > 0 && (
        <Banner kind="bad" title={`${data.dues.overdue} government payment${data.dues.overdue > 1 ? "s are" : " is"} overdue.`}
          action={<button onClick={() => go("/dues")}>See dues</button>}>
          Deposit them and record the challan numbers.
        </Banner>
      )}

      <div className="kpis">
        <Kpi icon="wallet" label={latest ? `Net pay · ${latest.label}` : "Net pay"} value={latest ? inr(latest.regular.net_pay) : "—"}
          hint={latestDelta ? `${signed(latestDelta)} vs ${before.label}` : latest ? latest.regular.status_label : "no month yet"}
          tone={latestDelta > 0 ? "up" : undefined} onClick={latest ? () => go(`/runs/${latest.regular.id}`) : undefined} />
        <Kpi icon="check" label="Last paid month" value={finals[0] ? finals[0].label : "None yet"}
          hint={finals.length ? `${finals.length} month${finals.length > 1 ? "s" : ""} final` : "approve a month to finish it"} />
      </div>

      <MonthView data={data} canOpen={canOpenNext} opening={opening} setOpening={setOpening} openForm={(
          <div className="open-month">
            <b>Open {data.next_period}</b>
            <Field label="Days for one day's pay">
              <input type="number" min="1" max="31" style={{ width: 90 }} value={dayCount} onChange={(e) => setDays(e.target.value)} />
            </Field>
            <button className="primary" disabled={!!busy || !dayCount || !data.approved_snapshot} onClick={() => open(data.next_period)}>{busy === "open" ? "Opening…" : "Open"}</button>
            <button className="ghost" onClick={() => setOpening(false)}>Cancel</button>
          </div>
      )} />


      <p className="muted small dash-foot">Updated {when(new Date().toISOString())} · <button className="ghost small" onClick={() => reload()}>Refresh</button></p>
    </div>
    {isChecker && <TodoPanel data={data} />}
    </div>
  );
}
