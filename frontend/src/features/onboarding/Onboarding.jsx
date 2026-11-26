import React, { useEffect, useState } from "react";
import { api } from "../../shared/api.js";
import { go } from "../../shared/routing.js";
import { Banner, Checks, Field, Loading, StatusChip, inr, monthLabel, useAction, useLoad } from "../../shared/ui.jsx";
import { AddConnector, Connector, UploadFiles } from "../hr_data/HRData.jsx";
import { OTHER_OFFICE, PF_OFFICES, splitPfCode } from "./pfOffices.js";

// Where the company is comes from the server (worked out from what exists), so the steps can be left and resumed.
const STEPS = [
  ["company", "Company details", "Name, state, registration numbers, first payroll month"],
  ["settings", "Pay settings", "Days for one day's pay, salary split, PF, government rules, AI"],
  ["hr_data", "HR data", "Connect the HR tool or upload its exports"],
  ["data_checked", "Check the data", "Read the checks and mark the data as right"],
  ["opening_balances", "Opening balances", "Pay and TDS earlier in the year"],
  ["first_month", "First month", "Open it and calculate"],
];

const firstMonthKey = (id) => `onboarding-first-month:${id}`;
function readFirstMonth(id) { try { return window.localStorage.getItem(firstMonthKey(id)) || ""; } catch { return ""; } }
function saveFirstMonth(id, value) { try { window.localStorage.setItem(firstMonthKey(id), value); } catch { /* private window: kept for this visit only */ } }
const thisMonth = () => new Date().toISOString().slice(0, 7);
const fyStart = (period) => { const [y, m] = period.split("-").map(Number); return `${m >= 4 ? y : y - 1}-04-01`; };

function done(steps, key, firstMonth) {
  if (!steps) return false;
  if (key === "company") return steps.company && steps.details_complete;
  if (key === "settings") return steps.settings;
  if (key === "hr_data") return steps.hr_data.done;
  if (key === "data_checked") return steps.data_checked.done;
  if (key === "opening_balances") return firstMonth.endsWith("-04") || steps.opening_balances.count > 0;
  return steps.first_month.done;
}

export default function Onboarding({ companyId, me, onCompanyCreated, onCompanyChanged }) {
  const isMaker = me.roles.includes("maker");
  if (!isMaker) return <Banner kind="info">Only a finance team employee can add or set up a company.</Banner>;
  if (!companyId) return <NewCompany onCreated={onCompanyCreated} />;
  return <Wizard key={companyId} companyId={companyId} onCompanyChanged={onCompanyChanged} />;
}

function NewCompany({ onCreated }) {
  const states = useLoad(() => api.get("/states"), []);
  return (
    <div>
      <div className="topbar"><div><h1>Add a company</h1></div></div>
      <div className="wizard">
        <StepList active="company" steps={null} firstMonth="" onPick={() => {}} />
        <div className="card">
          <h2>1. Company details</h2>
          <CompanyForm initial={{ name: "", state: "", city: "", pf_establishment_code: "", pay_day: "" }}
            states={states.data} firstMonth={thisMonth()} submitLabel="Create the company"
            onSave={async (body, first) => {
              const company = await api.post("/companies", body);
              saveFirstMonth(company.id, first);
              onCreated(company);
              return company;
            }} />
        </div>
      </div>
    </div>
  );
}

function StepList({ active, steps, firstMonth, onPick }) {
  return (
    <ol className="wizard-steps">
      {STEPS.map(([key, title, hint], i) => {
        const ok = done(steps, key, firstMonth);
        return (
          <li key={key} className={`${active === key ? "on" : ""} ${ok ? "ok" : ""}`}>
            <button type="button" disabled={!steps} onClick={() => onPick(key)}>
              <span className="dot">{ok ? "✓" : i + 1}</span>
              <span><b>{title}</b><span className="muted small">{hint}</span></span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function Wizard({ companyId, onCompanyChanged }) {
  const { data, error, reload } = useLoad(() => api.get(`/companies/${companyId}/onboarding`), [companyId]);
  const [active, setActive] = useState(null);
  const [firstMonth, setFirstMonthState] = useState(readFirstMonth(companyId) || thisMonth());
  const setFirstMonth = (v) => { setFirstMonthState(v); saveFirstMonth(companyId, v); };
  useEffect(() => {
    if (data?.steps.first_month.period) setFirstMonthState(data.steps.first_month.period);
  }, [data?.steps.first_month.period]);
  if (!data) return <Loading error={error} />;
  const { steps, company } = data;
  const current = active || STEPS.map(([k]) => k).find((k) => !done(steps, k, firstMonth)) || "first_month";
  const next = () => { reload(); const i = STEPS.findIndex(([k]) => k === current); setActive(STEPS[Math.min(i + 1, STEPS.length - 1)][0]); };
  const allDone = STEPS.every(([k]) => done(steps, k, firstMonth));
  return (
    <div>
      <div className="topbar"><div><h1>Set up {company.name}</h1>
        <p className="muted">First payroll month here: <b>{monthLabel(firstMonth)}</b>. Each step is saved as you go; the list shows what is done.</p></div></div>
      {allDone && <Banner kind="ok" title={`${company.name} is set up.`} action={<button className="primary" onClick={() => go(`/runs/${steps.first_month.run_id}`)}>Open {monthLabel(steps.first_month.period)}</button>}>
        From here each month works like any other: review the data, add changes, calculate, submit, approve.</Banner>}
      <div className="wizard">
        <StepList active={current} steps={steps} firstMonth={firstMonth} onPick={setActive} />
        <div className="card">
          {current === "company" && (
            <>
              <h2>1. Company details</h2>
              <CompanyForm initial={company} states={data.states} firstMonth={firstMonth} submitLabel="Save and continue"
                onSave={async (body, first) => {
                  const saved = await api.patch(`/companies/${companyId}`, body);
                  setFirstMonth(first);
                  onCompanyChanged(saved);
                  reload(true);  // the professional tax shown in step 2 follows the state just saved
                  next();
                  return saved;
                }} />
            </>
          )}
          {current === "settings" && <SettingsStep company={company} firstMonth={firstMonth} onDone={next} onCompanyChanged={onCompanyChanged}
            professionalTax={data.professional_tax} />}
          {current === "hr_data" && <HRDataStep companyId={companyId} steps={steps} onChange={reload} onDone={next} />}
          {current === "data_checked" && <CheckStep steps={steps} onDone={next} />}
          {current === "opening_balances" && <OpeningStep companyId={companyId} firstMonth={firstMonth} steps={steps} onDone={next} />}
          {current === "first_month" && <FirstMonthStep companyId={companyId} firstMonth={firstMonth} steps={steps} />}
        </div>
      </div>
    </div>
  );
}

function CompanyForm({ initial, states, firstMonth, submitLabel, onSave }) {
  const [f, setF] = useState({ ...initial, pay_day: initial.pay_day ?? "" });
  const [first, setFirst] = useState(firstMonth);
  const [pf, setPf] = useState(() => {
    const parts = splitPfCode(initial.pf_establishment_code);
    const listed = Object.values(PF_OFFICES).some((list) => list.some(([code]) => code === parts.office));
    return { ...parts, pick: !parts.office ? "" : listed ? parts.office : OTHER_OFFICE };
  });
  const [busy, act] = useAction();
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const offices = PF_OFFICES[f.state] || [];
  const otherStates = Object.entries(PF_OFFICES).filter(([st]) => st !== f.state);
  const stateName = (code) => (states || []).find((x) => x.code === code)?.name || code;
  const digits = (k, n) => (e) => setPf({ ...pf, [k]: e.target.value.replace(/\D/g, "").slice(0, n) });
  const pickOffice = (e) => {
    const v = e.target.value;
    setPf({ ...pf, pick: v, office: v === OTHER_OFFICE ? "" : v });
  };
  const pfCode = pf.office ? `${pf.office}${pf.number.padStart(7, "0")}${(pf.ext || "000").padStart(3, "0")}` : "";
  const pfComplete = !pf.office || (/^[A-Z]{5}$/.test(pf.office) && pf.number.length > 0);
  const save = () => act("save", () => onSave({
    name: f.name, state: f.state, city: f.city, pf_establishment_code: pf.number ? pfCode : "", pay_day: f.pay_day,
  }, first), "Company saved");
  return (
    <div className="stack">
      <div className="grid2">
        <Field label="Company name"><input value={f.name} onChange={set("name")} placeholder="e.g. Northwind Interiors Pvt Ltd" /></Field>
        <Field label="State of the main office">
          <select value={f.state} onChange={set("state")}>
            <option value="">Choose…</option>
            {(states || []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="City"><input value={f.city} onChange={set("city")} /></Field>
        <Field label="First payroll month on this system" hint="If it is not April, you will enter pay and TDS from earlier in the year.">
          <input type="month" value={first} onChange={(e) => setFirst(e.target.value)} />
        </Field>
        <Field label="PF regional office" hint={f.state ? "The office on your PF registration certificate" : "Choose the state first to see its offices"}>
          <select value={pf.pick} onChange={pickOffice}>
            <option value="">Choose…</option>
            {offices.length > 0 && <optgroup label={stateName(f.state)}>{offices.map(([code, name]) => <option key={code} value={code}>{code} · {name}</option>)}</optgroup>}
            {otherStates.map(([st, list]) => (
              <optgroup key={st} label={stateName(st)}>{list.map(([code, name]) => <option key={code} value={code}>{code} · {name}</option>)}</optgroup>
            ))}
            <option value={OTHER_OFFICE}>Other office (type its 5 letters)…</option>
          </select>
        </Field>
        <Field label="PF establishment number" hint={pfCode && pf.number ? `PF establishment code: ${pfCode}` : "7 digits, and a 3-digit extension (usually 000)"}>
          <div className="pf-code">
            {pf.pick === OTHER_OFFICE && <input aria-label="Office letters" placeholder="ABCDE" maxLength={5} value={pf.office}
              onChange={(e) => setPf({ ...pf, office: e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 5) })} />}
            <input aria-label="Establishment number" inputMode="numeric" placeholder="0012345" value={pf.number} onChange={digits("number", 7)} disabled={!pf.pick} />
            <input aria-label="Extension" inputMode="numeric" placeholder="000" value={pf.ext} onChange={digits("ext", 3)} disabled={!pf.pick} />
          </div>
        </Field>
        <Field label="Salary paid on (day of next month)" hint="Optional, e.g. 1"><input type="number" min="1" max="31" value={f.pay_day} onChange={set("pay_day")} /></Field>
      </div>
      <div className="row"><button className="primary" disabled={!!busy || !f.name || !f.state || !first || !pfComplete} onClick={save}>{busy ? "Saving…" : submitLabel}</button></div>
    </div>
  );
}

function latestPayload(groups, key) {
  const g = groups.find((x) => x.scope === "company" && x.key === key);
  return g?.versions.length ? g.versions[g.versions.length - 1].payload : g?.default;
}

// Which professional tax applies, from the state picked in step 1. The state's rule is applied automatically: by law
// PT follows where each employee works, and the company's state is used for anyone without a work location.
function ProfessionalTaxNote({ pt }) {
  if (!pt || pt.status === "no_state") return null;
  const slab = (x) => `${"above" in x ? `more than ${inr(x.above)}` : `${inr(x.min)} or more`}${(x.months || []).length ? ` (month ${x.months.join(", ")})` : ""}`;
  if (pt.status === "slabs") {
    return (
      <Banner kind={pt.warning ? "warn" : "info"} title={`Professional tax: ${pt.state_name}`}>
        <div>{pt.message}</div>
        <div className="small" style={{ marginTop: 4 }}>
          {(pt.slabs || []).filter((x) => Number(x.amount ?? 1) > 0 || x.rate).map((x, i) => (
            <span key={i}>{i > 0 && " · "}{slab(x)}: <b>{"rate" in x ? `${x.rate * 100}%` : inr(x.amount)}</b></span>
          ))}
          {pt.due_day ? ` · paid by the ${pt.due_day}th of the next month` : ""}
        </div>
        {pt.warning && <div className="small" style={{ marginTop: 4 }}>{pt.warning}</div>}
        {pt.verify && <div className="muted small" style={{ marginTop: 4 }}>{pt.verify}</div>}
      </Banner>
    );
  }
  return <Banner kind={pt.status === "not_charged" ? "info" : "warn"} title={`Professional tax: ${pt.state_name}`}>{pt.message}</Banner>;
}

function SettingsStep({ company, firstMonth, onDone, onCompanyChanged, professionalTax }) {
  const policies = useLoad(() => api.get(`/companies/${company.id}/policies`), [company.id]);
  const [f, setF] = useState(null);
  const [busy, act] = useAction();
  useEffect(() => {
    if (!policies.data || f) return;
    const lop = latestPayload(policies.data.groups, "lop") || { divisor: "days_in_month" };
    const split = latestPayload(policies.data.groups, "salary_structure")?.components || [];
    const share = (code, fallback) => String(Math.round(Number(split.find((c) => c.code === code)?.share ?? fallback) * 100));
    const pf = latestPayload(policies.data.groups, "pf_policy") || { wage_base: "capped" };
    const labour = latestPayload(policies.data.groups, "labour_codes") || { apply: "as_government" };
    setF({ days: lop.divisor === "days_in_month" ? "days_in_month" : String(lop.divisor), basic: share("basic", 0.5), hra: share("hra", 0.2),
      pf: pf.wage_base, labour: labour.apply, ai: company.ai_mode, saved: { lop, split, pf, labour } });
  }, [policies.data, f, company.ai_mode]);
  if (!f) return <Loading error={policies.error} />;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const fixed = f.days !== "days_in_month";
  const special = 100 - Number(f.basic || 0) - Number(f.hra || 0);
  const save = async () => {
    const from = fyStart(firstMonth);
    const lop = { ...f.saved.lop, divisor: fixed ? String(f.days) : "days_in_month" };
    const structure = { components: [{ code: "basic", share: (Number(f.basic) / 100).toFixed(2) }, { code: "hra", share: (Number(f.hra) / 100).toFixed(2) },
      { code: "special_allowance", share: "remainder" }] };
    const pf = { wage_base: f.pf, employer_wage_base: "same", arrears_contribution: "recompute_delta" };
    const labour = { apply: f.labour };
    const changed = [["lop", lop, f.saved.lop], ["salary_structure", structure, { components: f.saved.split }], ["pf_policy", pf, f.saved.pf], ["labour_codes", labour, f.saved.labour]]
      .filter(([, now, was]) => JSON.stringify(now) !== JSON.stringify(was) || !policies.data.groups.find((g) => g.scope === "company" && g.key === "lop")?.versions.length);
    const ok = await act("settings", async () => {
      for (const [key, payload] of changed) {
        await api.post(`/companies/${company.id}/policies`, { key, payload, effective_from: from, note: "Set when the company was added" });
      }
      if (f.ai !== company.ai_mode) onCompanyChanged(await api.patch(`/companies/${company.id}`, { ai_mode: f.ai }));
      return true;
    }, "Pay settings saved");
    if (ok) onDone();
  };
  return (
    <div className="stack">
      <h2>2. Pay settings</h2>
      <ProfessionalTaxNote pt={professionalTax} />
      <p className="muted">Saved as dated company settings from {fyStart(firstMonth)}. They can be changed later on Rules &amp; settings; each month can also set its own day count when it is opened.</p>
      <div className="grid2">
        <Field label="One day's pay = monthly pay ÷" hint="Used for unpaid days and for days before someone joined.">
          <select value={fixed ? "fixed" : "days_in_month"} onChange={(e) => setF({ ...f, days: e.target.value === "fixed" ? "30" : "days_in_month" })}>
            <option value="days_in_month">the days in that month (28 to 31)</option>
            <option value="fixed">a fixed number of days</option>
          </select>
        </Field>
        {fixed && <Field label="Number of days" hint="26 (working days) and 30 are common"><input type="number" min="1" max="31" value={f.days} onChange={set("days")} /></Field>}
        <Field label="Basic pay, % of monthly salary" hint="Splits a salary given as one amount"><input type="number" min="1" max="100" value={f.basic} onChange={set("basic")} /></Field>
        <Field label="HRA, % of monthly salary" hint={`Special allowance gets the rest: ${special}%`}><input type="number" min="0" max="100" value={f.hra} onChange={set("hra")} /></Field>
        <Field label="PF is worked out on">
          <select value={f.pf} onChange={set("pf")}>
            <option value="capped">basic pay up to ₹15,000 (the legal minimum)</option>
            <option value="full">all of basic pay</option>
          </select>
        </Field>
        <Field label="Government rules" hint={f.labour === "as_government"
          ? "The Labour Codes are in force from 21 Nov 2025: if pay that is not wages (HRA, allowances) is over 50% of total pay, the extra counts as wages for PF."
          : "Only if you have been advised the Labour Codes do not cover this company yet. PF stays on basic pay, and each payslip step says the rule was not applied."}>
          <select value={f.labour} onChange={set("labour")}>
            <option value="as_government">New Labour Codes (50% wage rule)</option>
            <option value="not_applied">Older labour laws (no 50% wage rule)</option>
          </select>
        </Field>
        <Field label="AI for reading HR data" hint="Off: only known HR tool profiles, rules and people map the data; nothing leaves the servers.">
          <select value={f.ai} onChange={set("ai")}>
            <option value="allowed">AI may be used</option>
            <option value="off">No AI</option>
          </select>
        </Field>
      </div>
      {special < 0 && <Banner kind="bad">Basic and HRA add up to more than 100%.</Banner>}
      <div className="row"><button className="primary" disabled={!!busy || special < 0 || (fixed && !(Number(f.days) >= 1 && Number(f.days) <= 31))} onClick={save}>{busy ? "Saving…" : "Save and continue"}</button></div>
    </div>
  );
}

function HRDataStep({ companyId, steps, onChange, onDone }) {
  const conns = useLoad(() => api.get(`/companies/${companyId}/connectors`), [companyId]);
  const job = steps.hr_data;
  return (
    <div className="stack">
      <h2>3. HR data</h2>
      <p className="muted">Employees, salaries and leave, from the HR tool's screens or its exports. You confirm what each column means once; later reads reuse it.</p>
      {job.done && (
        <Banner kind={job.status === "ready" ? "ok" : "info"} title={`Latest read #${job.job_id}:`}
          action={<button className="primary" onClick={() => go(`/data/job/${job.job_id}`)}>{job.status === "ready" ? "Open the read" : "Continue with it"}</button>}>
          <StatusChip status={job.status} /> {job.status === "needs_mapping" ? "confirm the columns, then build the data." : job.status === "ready" ? "columns confirmed." : ""}
        </Banner>
      )}
      <div className="grid2">
        <div className="card tight">
          <h3>Upload the exports</h3>
          <UploadFiles onUploaded={(j) => { onChange(); go(`/data/job/${j.id}`); }} send={(fd) => api.upload(`/companies/${companyId}/uploads`, fd)} compact />
        </div>
        <div className="card tight">
          <h3>Or connect the HR tool</h3>
          {!conns.data ? <Loading error={conns.error} /> : conns.data.map((c) => <Connector key={c.id} conn={c} isMaker onChange={conns.reload} />)}
          <AddConnector companyId={companyId} onAdded={conns.reload} />
        </div>
      </div>
      {steps.data_checked.snapshot_id && <div className="row"><button className="primary" onClick={onDone}>Continue</button></div>}
    </div>
  );
}

function CheckStep({ steps, onDone }) {
  const id = steps.data_checked.snapshot_id;
  const snap = useLoad(() => (id ? api.get(`/snapshots/${id}`) : Promise.resolve(null)), [id]);
  if (!id) return <><h2>4. Check the data</h2><Banner kind="warn">Read the HR data first (step 3), confirm its columns and build the data.</Banner></>;
  if (!snap.data) return <Loading error={snap.error} />;
  const s = snap.data;
  return (
    <div className="stack">
      <h2>4. Check the data</h2>
      <p className="muted">{s.employee_count} employees from {s.source_system}. Warnings often change pay (leave with no type counts as unpaid, a missing joining date pays a full month), so read them.</p>
      <Checks checks={s.checks.filter((c) => c.level !== "ok").concat(s.checks.filter((c) => c.level === "ok"))} />
      {s.status === "approved"
        ? <div className="row"><span className="chip ok">Marked as right</span><button className="primary" onClick={onDone}>Continue</button></div>
        : <div className="row"><button className="primary" onClick={() => go(`/data/snapshot/${s.id}`)}>Review and mark as right</button>
          <span className="muted small">Opens the data, with every employee, salary and leave row.</span></div>}
    </div>
  );
}

function OpeningStep({ companyId, firstMonth, steps, onDone }) {
  const april = firstMonth.endsWith("-04");
  const summary = useLoad(() => (april ? Promise.resolve(null) : api.get(`/companies/${companyId}/opening-balances?period=${firstMonth}`)), [companyId, firstMonth]);
  const [file, setFile] = useState(null);
  const [round, setRound] = useState(0);
  const [busy, act] = useAction();
  if (april) return (
    <div className="stack"><h2>5. Opening balances</h2>
      <Banner kind="ok">Not needed: payroll starts in April, the first month of the financial year.</Banner>
      <div className="row"><button className="primary" onClick={onDone}>Continue</button></div></div>
  );
  if (!steps.data_checked.done) return <><h2>5. Opening balances</h2><Banner kind="warn">Mark the HR data as right first (step 4): the balances are checked against its employees.</Banner></>;
  const upload = async () => {
    const fd = new FormData();
    fd.append("period", firstMonth);
    fd.append("file", file);
    const ok = await act("upload", () => api.upload(`/companies/${companyId}/opening-balances`, fd), "Opening balances saved");
    if (ok) { setFile(null); setRound(round + 1); summary.reload(); }
  };
  const s = summary.data;
  return (
    <div className="stack">
      <h2>5. Opening balances</h2>
      <p className="muted">Payroll starts here in {monthLabel(firstMonth)}, so the old payroll already paid salaries and deducted TDS from April. Enter each person's totals so far; TDS for the rest of the year is spread from them. A new upload replaces the earlier one.</p>
      <div className="row" key={round}>
        <a className="button" href={`/api/companies/${companyId}/opening-balances?template=1`}>Download the template</a>
        <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files[0] || null)} />
        <button className="primary" disabled={!!busy || !file} onClick={upload}>{busy ? "Checking…" : "Upload"}</button>
      </div>
      <p className="muted small">Columns: Employee ID, Gross paid this year, TDS this year.</p>
      {!s ? <Loading error={summary.error} /> : s.count > 0 && (
        <>
          <div className="grid4">
            <div className="stat"><div className="label">People</div><div className="value">{s.count}</div></div>
            <div className="stat"><div className="label">Gross paid {s.covers}</div><div className="value">{inr(s.gross)}</div></div>
            <div className="stat"><div className="label">TDS deducted</div><div className="value">{inr(s.tds)}</div></div>
            <div className="stat"><div className="label">Financial year</div><div className="value">{s.financial_year}</div></div>
          </div>
          {s.missing.length > 0 && <Banner kind="warn" title={`${s.missing.length} people joined before ${monthLabel(firstMonth)} but have no balance:`}>
            {s.missing.map((m) => `${m.name} (${m.employee_code})`).join(", ")}. Their TDS will assume nothing was paid earlier in the year.</Banner>}
          <div className="table-wrap" style={{ maxHeight: 280 }}><table>
            <thead><tr><th>Employee</th><th className="num">Gross paid</th><th className="num">TDS deducted</th></tr></thead>
            <tbody>{s.rows.map((r) => <tr key={r.employee_code}><td>{r.name} <span className="muted small">{r.employee_code}</span></td><td className="num">{inr(r.gross)}</td><td className="num">{inr(r.tds)}</td></tr>)}</tbody>
          </table></div>
          <div className="row"><button className="primary" onClick={onDone}>Continue</button></div>
        </>
      )}
    </div>
  );
}

function FirstMonthStep({ companyId, firstMonth, steps }) {
  const preset = useLoad(() => api.get(`/companies/${companyId}/day-count?period=${firstMonth}`), [companyId, firstMonth]);
  const [days, setDays] = useState("");
  const [busy, act] = useAction();
  if (steps.first_month.done) return (
    <div className="stack"><h2>6. First month</h2>
      <Banner kind="ok" title={`${monthLabel(steps.first_month.period)} is open.`}>Review the data, calculate, and send it for approval like any month.</Banner>
      <div className="row"><button className="primary" onClick={() => go(`/runs/${steps.first_month.run_id}`)}>Open {monthLabel(steps.first_month.period)}</button></div></div>
  );
  if (!steps.data_checked.done) return <><h2>6. First month</h2><Banner kind="warn">Mark the HR data as right first (step 4).</Banner></>;
  const shown = days || (preset.data ? String(preset.data.days) : "");
  const open = async () => {
    const run = await act("open", () => api.post(`/companies/${companyId}/runs`, { period: firstMonth, day_count: shown }), `${monthLabel(firstMonth)} opened`);
    if (run) go(`/runs/${run.id}`);
  };
  return (
    <div className="stack">
      <h2>6. First month</h2>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <Field label="Month"><input value={monthLabel(firstMonth)} readOnly /></Field>
        <Field label="Days for one day's pay">
          <input type="number" min="1" max="31" value={shown} onChange={(e) => setDays(e.target.value)} />
        </Field>
        <button className="primary" disabled={!!busy || !shown} onClick={open}>Open {monthLabel(firstMonth)}</button>
      </div>
    </div>
  );
}
