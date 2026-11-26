import React, { useState } from "react";
import { api } from "../../shared/api.js";
import { DataView, Field, inr, useLoad } from "../../shared/ui.jsx";

// Forms and plain-language summaries for every rule and company setting, so nobody has to read or write
// JSON. Rates are stored as fractions ("0.0325") and shown as percentages (3.25%); the conversion moves
// the decimal point in the text, never through floating point. Fields a form does not know are kept.

export const COMPONENTS = [
  ["basic", "Basic"], ["da", "Dearness allowance"], ["hra", "HRA"],
  ["special_allowance", "Special allowance"], ["conveyance", "Conveyance"], ["other_allowance", "Other allowance"],
];
const ROUNDING = [
  ["nearest", "To the nearest rupee (₹0.50 goes up)"], ["up", "Always up"], ["down", "Always down"],
  ["nearest_even", "To the nearest rupee (₹0.50 to the even rupee)"],
];
const WAGES = [
  ["gross_this_month", "Gross pay this month"], ["gross_excluding_one_time", "Gross pay without one-time payments"],
  ["regular_earned", "Regular pay earned this month"], ["contracted_monthly", "Full monthly salary (before unpaid days)"],
];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const GENDERS = [["", "Everyone"], ["M", "Men"], ["F", "Women"], ["O", "Others"]];
const label = (options, value) => (options.find(([v]) => String(v) === String(value)) || [value, value])[1];
const componentNames = (codes) => (codes || []).map((c) => label(COMPONENTS, c)).join(" + ") || "—";

// "0.0325" -> "3.25" (places = 2) and "3.25" -> "0.0325" (places = -2), exactly.
function shift(value, places) {
  if (value === null || value === undefined || value === "") return "";
  let s = String(value).trim();
  const neg = s.startsWith("-");
  if (neg) s = s.slice(1);
  if (!/^\d*\.?\d*$/.test(s) || s === "" || s === ".") return String(value);
  const [i, f = ""] = s.split(".");
  let digits = (i || "0") + f;
  let point = (i || "0").length + places;
  if (point <= 0) { digits = "0".repeat(1 - point) + digits; point = 1; }
  if (point > digits.length) digits += "0".repeat(point - digits.length);
  const whole = digits.slice(0, point).replace(/^0+(?=\d)/, "");
  const part = digits.slice(point).replace(/0+$/, "");
  return (neg ? "-" : "") + whole + (part ? `.${part}` : "");
}
export const toPercent = (fraction) => shift(fraction, 2);
export const fromPercent = (percent) => (percent === "" ? "" : shift(percent, -2));
const pct = (fraction) => (fraction === undefined || fraction === null || fraction === "" ? "—" : `${toPercent(fraction)}%`);
function ordinal(n) {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return `${n}${teen ? "th" : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"}`;
}
const day = (n) => (n ? `the ${ordinal(Number(n))} of the next month` : "—");

// ------------------------------------------------------------------ inputs
function Money({ value, onChange, placeholder }) {
  return (
    <span className="affix"><span>₹</span>
      <input inputMode="decimal" value={value ?? ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value.replace(/[,\s₹]/g, ""))} />
    </span>
  );
}
// Keeps what is being typed ("3." on the way to "3.25") while the stored value is the fraction.
export function PercentInput({ value, onChange, ...rest }) {
  const [text, setText] = React.useState(toPercent(value));
  React.useEffect(() => {
    if (fromPercent(text) !== String(value ?? "")) setText(toPercent(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input inputMode="decimal" value={text} {...rest}
      onChange={(e) => { const t = e.target.value.replace(/[%\s]/g, ""); setText(t); onChange(fromPercent(t)); }} />
  );
}
function Percent({ value, onChange }) {
  return <span className="affix"><PercentInput value={value} onChange={onChange} /><span>%</span></span>;
}
function Choice({ value, options, onChange }) {
  return <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>;
}
function Check({ checked, onChange, children }) {
  return <label className="check"><input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} /> {children}</label>;
}
function Components({ value, onChange }) {
  const has = new Set(value || []);
  return (
    <div className="row" style={{ gap: 14 }}>
      {COMPONENTS.map(([code, name]) => (
        <Check key={code} checked={has.has(code)} onChange={(on) => onChange(COMPONENTS.map(([c]) => c).filter((c) => (c === code ? on : has.has(c))))}>{name}</Check>
      ))}
    </div>
  );
}
function DueDay({ value, onChange }) {
  return <Field label="Pay to the government by (day of next month)"><input inputMode="numeric" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value) || e.target.value)} /></Field>;
}

// ------------------------------------------------------------------ forms
export function RuleForm({ ruleKey, value, onChange }) {
  const v = value || {};
  const set = (k) => (x) => onChange({ ...v, [k]: x });
  switch (ruleKey) {
    case "pf":
      return (
        <div className="grid3">
          <Field label="Employee share"><Percent value={v.employee_rate} onChange={set("employee_rate")} /></Field>
          <Field label="Company share"><Percent value={v.employer_rate} onChange={set("employer_rate")} /></Field>
          <Field label="Wage ceiling (PF is worked out on pay up to this)"><Money value={v.wage_ceiling} onChange={set("wage_ceiling")} /></Field>
          <Field label="Pension (EPS) part of the company share"><Percent value={v.eps_rate} onChange={set("eps_rate")} /></Field>
          <Field label="Pension wage ceiling"><Money value={v.eps_wage_ceiling} onChange={set("eps_wage_ceiling")} /></Field>
          <Field label="Rounding"><Choice value={v.rounding || "nearest"} options={ROUNDING} onChange={set("rounding")} /></Field>
          <div className="span-all"><Field label="Pay that counts as PF wages"><Components value={v.wage_components} onChange={set("wage_components")} /></Field></div>
          <DueDay value={v.due_day_next_month} onChange={set("due_day_next_month")} />
        </div>
      );
    case "esi":
      return (
        <div className="grid3">
          <Field label="Covered when monthly pay is at most"><Money value={v.threshold} onChange={set("threshold")} /></Field>
          <Field label="Employee share"><Percent value={v.employee_rate} onChange={set("employee_rate")} /></Field>
          <Field label="Company share"><Percent value={v.employer_rate} onChange={set("employer_rate")} /></Field>
          <Field label="Rounding"><Choice value={v.rounding || "up"} options={ROUNDING} onChange={set("rounding")} /></Field>
          <DueDay value={v.due_day_next_month} onChange={set("due_day_next_month")} />
        </div>
      );
    case "professional_tax":
      return <ProfessionalTaxForm v={v} onChange={onChange} />;
    case "income_tax":
      return <IncomeTaxForm v={v} onChange={onChange} />;
    case "wage_definition":
      return (
        <div className="stack">
          <Check checked={v.enabled} onChange={set("enabled")}>Apply the rule</Check>
          <div className="grid3">
            <Field label="Pay that is not wages may be at most this share of total pay; the rest counts as wages"><Percent value={v.max_exclusion_share} onChange={set("max_exclusion_share")} /></Field>
          </div>
          <Field label="Pay that counts as wages"><Components value={v.wage_components} onChange={set("wage_components")} /></Field>
          <Field label="Used for"><Check checked={(v.applies_to || ["pf"]).includes("pf")} onChange={(on) => set("applies_to")(on ? ["pf"] : [])}>Provident Fund</Check></Field>
        </div>
      );
    case "lop": {
      const preset = ["days_in_month", "30", "26"].includes(String(v.divisor)) ? String(v.divisor) : "custom";
      const r = v.day_rate_rounding;
      return (
        <div className="grid3">
          <Field label="One day of pay is the month's pay divided by">
            <Choice value={preset} options={[["days_in_month", "The days in that month"], ["30", "30"], ["26", "26"], ["custom", "Another number…"]]}
              onChange={(x) => set("divisor")(x === "days_in_month" ? x : x === "custom" ? "" : Number(x))} />
          </Field>
          {preset === "custom" && <Field label="Divide by"><input inputMode="numeric" value={v.divisor ?? ""} onChange={(e) => set("divisor")(e.target.value)} /></Field>}
          <Field label="Round one day's pay">
            <Choice value={r ? String(r.unit) : ""} options={[["", "Not rounded"], ["0.01", "To the paisa"], ["1", "To the rupee"]]}
              onChange={(x) => set("day_rate_rounding")(x ? { unit: x, method: (r && r.method) || "nearest" } : null)} />
          </Field>
          {r && <Field label="Rounding direction"><Choice value={r.method} options={ROUNDING} onChange={(m) => set("day_rate_rounding")({ ...r, method: m })} /></Field>}
        </div>
      );
    }
    case "rounding":
      return (
        <div className="grid3">
          <Field label="What is rounded"><Choice value={v.earnings} options={[["per_component", "Each part of pay"], ["gross_only", "Only the total"]]} onChange={set("earnings")} /></Field>
          <Field label="Direction"><Choice value={v.method} options={ROUNDING} onChange={set("method")} /></Field>
        </div>
      );
    case "pf_policy":
      return (
        <div className="grid3">
          <Field label="Employee PF is worked out on"><Choice value={v.wage_base} options={[["capped", "PF wages up to the ceiling"], ["full", "All PF wages"]]} onChange={set("wage_base")} /></Field>
          <Field label="Company PF is worked out on"><Choice value={v.employer_wage_base || "same"} options={[["same", "The same as the employee"], ["capped", "PF wages up to the ceiling"], ["full", "All PF wages"]]} onChange={set("employer_wage_base")} /></Field>
          <Field label="PF on arrears"><Choice value={v.arrears_contribution || "recompute_delta"} options={[["recompute_delta", "Yes: the difference is added"], ["none", "No PF on arrears"]]} onChange={set("arrears_contribution")} /></Field>
        </div>
      );
    case "esi_policy":
      return (
        <div className="grid3">
          <Field label="Who is covered is decided on"><Choice value={v.eligibility_wage} options={WAGES} onChange={set("eligibility_wage")} /></Field>
          <Field label="ESI is worked out on"><Choice value={v.contribution_wage} options={WAGES} onChange={set("contribution_wage")} /></Field>
        </div>
      );
    case "pt_policy":
      return <div className="grid3"><Field label="Professional tax slab is picked from"><Choice value={v.basis} options={WAGES} onChange={set("basis")} /></Field></div>;
    case "tds_policy":
      return (
        <div className="grid3">
          <Field label="Monthly tax">{/* the only method the engine has */}<input value="Tax for the projected year, spread over the months left" disabled /></Field>
          <Field label="Tax on a bonus or other one-time pay"><Choice value={v.one_time_income} options={[["spread", "Spread over the months left in the year"], ["tax_in_month", "Taken in the month it is paid"]]} onChange={set("one_time_income")} /></Field>
          <Field label="Round the year's tax to"><Choice value={String(v.annual_tax_rounding_unit ?? 1)} options={[["1", "The rupee"], ["10", "The nearest ₹10"]]} onChange={(x) => set("annual_tax_rounding_unit")(Number(x))} /></Field>
          <Field label="Round each month's tax"><Choice value={v.monthly_rounding || "nearest"} options={ROUNDING} onChange={set("monthly_rounding")} /></Field>
        </div>
      );
    case "arrears_policy":
      return (
        <div className="grid3">
          <Check checked={v.enabled !== false} onChange={set("enabled")}>Pay arrears when a raise reaches back to months already paid</Check>
          <Field label="Look back at most (months)"><input inputMode="numeric" value={v.lookback_months ?? ""} onChange={(e) => set("lookback_months")(e.target.value === "" ? undefined : Number(e.target.value))} /></Field>
        </div>
      );
    case "salary_structure":
      return <SalaryStructureForm v={v} onChange={onChange} />;
    case "work_calendar":
      return <WorkCalendarForm v={v} onChange={onChange} />;
    default:
      return <p className="muted small">This setting has no form yet. Use “Edit as JSON” below.</p>;
  }
}

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const cap = (word) => word.charAt(0).toUpperCase() + word.slice(1);

// Which days are not working days, so a day with no check-in is not suggested as an absence.
function WorkCalendarForm({ v, onChange }) {
  const off = v.weekly_off || [];
  const holidays = v.holidays || [];
  const [day, setDay] = useState("");
  const toggle = (d) => onChange({ ...v, weekly_off: off.includes(d) ? off.filter((x) => x !== d) : WEEKDAYS.filter((x) => off.includes(x) || x === d) });
  const add = () => { if (day && !holidays.includes(day)) onChange({ ...v, holidays: [...holidays, day].sort() }); setDay(""); };
  return (
    <div className="stack">
      <Field label="Weekly days off">
        <div className="row">{WEEKDAYS.map((d) => <Check key={d} checked={off.includes(d)} onChange={() => toggle(d)}>{cap(d)}</Check>)}</div>
      </Field>
      <Field label="Company holidays" hint="Days with no check-in on these dates are not suggested as absences.">
        <div className="row">
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
          <button type="button" onClick={add} disabled={!day}>Add holiday</button>
        </div>
      </Field>
      {holidays.length > 0 && (
        <div className="row" style={{ flexWrap: "wrap" }}>
          {holidays.map((h) => (
            <span key={h} className="chip">{h} <button type="button" className="ghost small" aria-label={`Remove ${h}`} onClick={() => onChange({ ...v, holidays: holidays.filter((x) => x !== h) })}>✕</button></span>
          ))}
        </div>
      )}
    </div>
  );
}

function RowButtons({ onRemove }) {
  return <td><button type="button" className="ghost small" onClick={onRemove} title="Remove this row">Remove</button></td>;
}

function ProfessionalTaxForm({ v, onChange }) {
  const states = useLoad(() => api.get("/states"), []); // every state and union territory, the same list the server uses
  const slabs = v.slabs || [];
  const put = (i, slab) => onChange({ ...v, slabs: slabs.map((s, k) => (k === i ? slab : s)) });
  const without = (slab, ...keys) => Object.fromEntries(Object.entries(slab).filter(([k]) => !keys.includes(k)));
  return (
    <div className="stack">
      <div className="grid3">
        <Field label="State name">
          <select value={v.state_name || ""} onChange={(e) => onChange({ ...v, state_name: e.target.value })}>
            <option value="">Choose a state</option>
            {(states.data || []).map((st) => <option key={st.code} value={st.name}>{st.name}</option>)}
            {v.state_name && states.data && !states.data.some((st) => st.name === v.state_name) && <option value={v.state_name}>{v.state_name}</option>}
          </select>
        </Field>
        <DueDay value={v.due_day_next_month} onChange={(x) => onChange({ ...v, due_day_next_month: x })} />
      </div>
      <div className="table-wrap">
        <table className="form-table">
          <thead><tr><th>When monthly pay is</th><th>Up to (optional)</th><th>Tax</th><th>For</th><th>In</th><th></th></tr></thead>
          <tbody>
            {slabs.map((s, i) => {
              const strict = "above" in s;
              const byRate = "rate" in s;
              return (
                <tr key={i}>
                  <td><div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                    <Choice value={strict ? "above" : "min"} options={[["min", "₹ or more"], ["above", "more than ₹"]]}
                      onChange={(x) => put(i, { ...without(s, "min", "above"), [x]: s.min ?? s.above ?? "0" })} />
                    <Money value={strict ? s.above : s.min} onChange={(x) => put(i, { ...s, [strict ? "above" : "min"]: x })} />
                  </div></td>
                  <td><Money value={s.max ?? ""} placeholder="no limit" onChange={(x) => put(i, x === "" ? without(s, "max") : { ...s, max: x })} /></td>
                  <td><div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                    <Choice value={byRate ? "rate" : "amount"} options={[["amount", "Fixed ₹"], ["rate", "% of pay"]]}
                      onChange={(x) => put(i, { ...without(s, "amount", "rate"), [x]: "0" })} />
                    {byRate ? <Percent value={s.rate} onChange={(x) => put(i, { ...s, rate: x })} /> : <Money value={s.amount} onChange={(x) => put(i, { ...s, amount: x })} />}
                  </div></td>
                  <td><Choice value={s.gender || ""} options={GENDERS} onChange={(x) => put(i, x ? { ...s, gender: x } : without(s, "gender"))} /></td>
                  <td>
                    {(s.months || []).length > 1
                      ? <span className="small">{s.months.map((m) => MONTHS[m - 1]).join(", ")}</span>
                      : <Choice value={String((s.months || [])[0] || "")} options={[["", "Every month"], ...MONTHS.map((m, k) => [String(k + 1), `${m} only`])]}
                          onChange={(x) => put(i, x ? { ...s, months: [Number(x)] } : without(s, "months"))} />}
                  </td>
                  <RowButtons onRemove={() => onChange({ ...v, slabs: slabs.filter((_, k) => k !== i) })} />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div><button type="button" className="small" onClick={() => onChange({ ...v, slabs: [...slabs, { min: "", amount: "" }] })}>+ Add a slab</button></div>
    </div>
  );
}

function IncomeTaxForm({ v, onChange }) {
  const slabs = v.slabs || [];
  const rebate = v.rebate || null;
  const surcharge = v.surcharge || [];
  const putSlab = (i, slab) => onChange({ ...v, slabs: slabs.map((s, k) => (k === i ? slab : s)) });
  const putSur = (i, row) => onChange({ ...v, surcharge: surcharge.map((s, k) => (k === i ? row : s)) });
  return (
    <div className="stack">
      <div className="grid3">
        <Field label="Tax regime"><input value="New regime" disabled /></Field>
        <Field label="Standard deduction"><Money value={v.standard_deduction} onChange={(x) => onChange({ ...v, standard_deduction: x })} /></Field>
        <Field label="Health and education cess"><Percent value={v.cess_rate} onChange={(x) => onChange({ ...v, cess_rate: x })} /></Field>
        <DueDay value={v.due_day_next_month} onChange={(x) => onChange({ ...v, due_day_next_month: x })} />
      </div>
      <h3 className="form-h">Slabs (yearly taxable income)</h3>
      <div className="table-wrap">
        <table className="form-table">
          <thead><tr><th>Income from</th><th>Up to</th><th>Tax rate</th><th></th></tr></thead>
          <tbody>
            {slabs.map((s, i) => {
              const last = i === slabs.length - 1;
              return (
                <tr key={i}>
                  <td className="small">{i === 0 ? "₹0" : inr(slabs[i - 1].upto)}</td>
                  <td>{last ? <span className="small">and above</span> : <Money value={s.upto} onChange={(x) => putSlab(i, { ...s, upto: x })} />}</td>
                  <td><Percent value={s.rate} onChange={(x) => putSlab(i, { ...s, rate: x })} /></td>
                  {last || slabs.length < 3 ? <td /> : <RowButtons onRemove={() => onChange({ ...v, slabs: slabs.filter((_, k) => k !== i) })} />}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div><button type="button" className="small" onClick={() => onChange({ ...v, slabs: [...slabs.slice(0, -1), { upto: "", rate: "" }, ...slabs.slice(-1)] })}>+ Add a slab</button></div>
      <h3 className="form-h">Rebate (section 87A)</h3>
      <Check checked={!!rebate} onChange={(on) => onChange({ ...v, rebate: on ? { income_upto: "", max_rebate: "", marginal_relief: true } : null })}>There is a rebate</Check>
      {rebate && (
        <div className="grid3">
          <Field label="For taxable income up to"><Money value={rebate.income_upto} onChange={(x) => onChange({ ...v, rebate: { ...rebate, income_upto: x } })} /></Field>
          <Field label="Rebate of at most"><Money value={rebate.max_rebate} onChange={(x) => onChange({ ...v, rebate: { ...rebate, max_rebate: x } })} /></Field>
          <Check checked={rebate.marginal_relief} onChange={(x) => onChange({ ...v, rebate: { ...rebate, marginal_relief: x } })}>Marginal relief just above the limit</Check>
        </div>
      )}
      <h3 className="form-h">Surcharge</h3>
      <div className="table-wrap">
        <table className="form-table">
          <thead><tr><th>Income above</th><th>Surcharge on the tax</th><th></th></tr></thead>
          <tbody>
            {surcharge.map((s, i) => (
              <tr key={i}>
                <td><Money value={s.above} onChange={(x) => putSur(i, { ...s, above: x })} /></td>
                <td><Percent value={s.rate} onChange={(x) => putSur(i, { ...s, rate: x })} /></td>
                <RowButtons onRemove={() => onChange({ ...v, surcharge: surcharge.filter((_, k) => k !== i) })} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row">
        <button type="button" className="small" onClick={() => onChange({ ...v, surcharge: [...surcharge, { above: "", rate: "" }] })}>+ Add a surcharge level</button>
        <Check checked={v.surcharge_marginal_relief} onChange={(x) => onChange({ ...v, surcharge_marginal_relief: x })}>Marginal relief on surcharge</Check>
      </div>
    </div>
  );
}

function SalaryStructureForm({ v, onChange }) {
  const comps = v.components || [];
  const put = (i, c) => onChange({ ...v, components: comps.map((x, k) => (k === i ? c : x)) });
  return (
    <div className="stack">
      <div className="table-wrap">
        <table className="form-table">
          <thead><tr><th>Part of pay</th><th>Share of monthly gross</th><th></th></tr></thead>
          <tbody>
            {comps.map((c, i) => (
              <tr key={i}>
                <td><Choice value={c.code} options={COMPONENTS} onChange={(x) => put(i, { ...c, code: x })} /></td>
                <td><div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                  <Choice value={c.share === "remainder" ? "rest" : "share"} options={[["share", "A fixed share"], ["rest", "Whatever is left"]]}
                    onChange={(x) => onChange({ ...v, components: comps.map((y, k) => (k === i ? { ...y, share: x === "rest" ? "remainder" : "" } : x === "rest" && y.share === "remainder" ? { ...y, share: "" } : y)) })} />
                  {c.share !== "remainder" && <Percent value={c.share} onChange={(x) => put(i, { ...c, share: x })} />}
                </div></td>
                <RowButtons onRemove={() => onChange({ ...v, components: comps.filter((_, k) => k !== i) })} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div><button type="button" className="small" onClick={() => onChange({ ...v, components: [...comps, { code: "conveyance", share: "" }] })}>+ Add a part</button></div>
      <p className="muted small" style={{ margin: 0 }}>Used when a salary comes as one monthly figure (for example a contract wage). Exactly one part takes whatever is left.</p>
    </div>
  );
}

// ------------------------------------------------------------------ summaries
function Lines({ items }) {
  return <ul className="summary">{items.filter(Boolean).map((t, i) => <li key={i}>{t}</li>)}</ul>;
}

// Every stored value of a rule, one per line with a readable name, for the Details panel. Rates show as percentages,
// money as rupees, the due day as a date, choices by their names; lists of slabs are summed up (they are shown above).
const VALUE_NAMES = {
  threshold: "Covers pay up to", employee_rate: "Employee rate", employer_rate: "Company rate", rounding: "Rounding",
  due_day_next_month: "Paid by", wage_ceiling: "Wage ceiling", eps_rate: "Pension (EPS) rate", eps_wage_ceiling: "Pension wage ceiling",
  wage_components: "Counts as wages", standard_deduction: "Standard deduction", cess_rate: "Cess", regime: "Tax regime",
  state_name: "State", divisor: "One day's pay divides by", enabled: "In use", max_exclusion_share: "Most pay left out of wages",
  applies_to: "Applies to", slabs: "Slabs", surcharge: "Surcharge bands", rebate: "Rebate",
};
const isMoneyKey = (k) => /threshold|ceiling|deduction|amount|limit|upto|above|min|max(?!_exclusion)/.test(k);
function valueText(k, v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (k === "due_day_next_month") return day(v);
  if (k === "rounding" || k === "method") return label(ROUNDING, v);
  if (k === "wage_components") return componentNames(v);
  if (Array.isArray(v)) return v.length && typeof v[0] === "object" ? `${v.length} row${v.length > 1 ? "s" : ""}` : v.join(", ").toUpperCase();
  if (typeof v === "object") return Object.entries(v).map(([kk, vv]) => `${(VALUE_NAMES[kk] || kk.replace(/_/g, " "))}: ${valueText(kk, vv)}`).join(" · ");
  if (/rate|share/.test(k)) return pct(v);
  if (isMoneyKey(k) && !Number.isNaN(Number(v))) return inr(v);
  return String(v);
}
export function RuleValues({ payload }) {
  const rows = Object.entries(payload || {});
  if (!rows.length) return null;
  return (
    <dl className="rule-values">
      {rows.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt>{VALUE_NAMES[k] || k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " ")}</dt>
          <dd>{valueText(k, v)}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function RuleSummary({ ruleKey, payload }) {
  const v = payload || {};
  switch (ruleKey) {
    case "pf":
      return <Lines items={[
        `Employee ${pct(v.employee_rate)} and company ${pct(v.employer_rate)} of ${componentNames(v.wage_components)}, on pay up to ${inr(v.wage_ceiling)}.`,
        v.eps_rate && `Pension (EPS): ${pct(v.eps_rate)} of pay up to ${inr(v.eps_wage_ceiling)}, out of the company share.`,
        `Rounded ${label(ROUNDING, v.rounding || "nearest").toLowerCase()}. Paid by ${day(v.due_day_next_month)}.`,
      ]} />;
    case "esi":
      return <Lines items={[
        `Covers employees paid up to ${inr(v.threshold)} a month.`,
        `Employee ${pct(v.employee_rate)}, company ${pct(v.employer_rate)}. Rounded ${label(ROUNDING, v.rounding || "up").toLowerCase()}. Paid by ${day(v.due_day_next_month)}.`,
      ]} />;
    case "professional_tax":
      if (v.levied === false) return <Lines items={[`${v.state_name || "This state"} does not charge professional tax on salaries.`]} />;
      return (
        <div>
          <table className="mini">
            <tbody>
              {(v.slabs || []).map((s, i) => (
                <tr key={i}>
                  <td>{"above" in s ? `More than ${inr(s.above)}` : `${inr(s.min)} or more`}{s.max != null ? `, up to ${inr(s.max)}` : ""}
                    {s.gender ? ` · ${label(GENDERS, s.gender).toLowerCase()}` : ""}{(s.months || []).length ? ` · in ${s.months.map((m) => MONTHS[m - 1]).join(", ")}` : ""}</td>
                  <td style={{ textAlign: "right" }}><b>{"rate" in s ? pct(s.rate) : inr(s.amount)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted small">{v.state_name}. {v.due_day_next_month
            ? <>Paid by {day(v.due_day_next_month)}.</>
            : <span className="chip warn">No due day: add one before a month with this rule is finalised</span>}</div>
        </div>
      );
    case "income_tax": {
      const slabs = v.slabs || [];
      return (
        <div>
          <table className="mini">
            <tbody>
              {slabs.map((s, i) => (
                <tr key={i}>
                  <td>{i === 0 ? "₹0" : inr(slabs[i - 1].upto)} – {s.upto == null ? "and above" : inr(s.upto)}</td>
                  <td style={{ textAlign: "right" }}><b>{pct(s.rate)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          <Lines items={[
            `Standard deduction ${inr(v.standard_deduction)}.`,
            v.rebate && `No tax up to ${inr(v.rebate.income_upto)} of taxable income (rebate up to ${inr(v.rebate.max_rebate)}${v.rebate.marginal_relief ? ", with marginal relief" : ""}).`,
            (v.surcharge || []).length > 0 && `Surcharge: ${v.surcharge.map((s) => `${pct(s.rate)} above ${inr(s.above)}`).join(", ")}.`,
            `Cess ${pct(v.cess_rate)}. Paid by ${day(v.due_day_next_month)}.`,
          ]} />
        </div>
      );
    }
    case "wage_definition":
      return <Lines items={[
        v.enabled ? "In use." : "Not in use.",
        `If pay other than ${componentNames(v.wage_components)} is more than ${pct(v.max_exclusion_share)} of total pay, the extra counts as wages${(v.applies_to || ["pf"]).includes("pf") ? " for PF" : ""}.`,
      ]} />;
    case "lop":
      return <Lines items={[
        `One day's pay = the month's pay ÷ ${v.divisor === "days_in_month" ? "the days in that month" : v.divisor}.`,
        v.day_rate_rounding ? `Rounded ${v.day_rate_rounding.unit === "1" || v.day_rate_rounding.unit === 1 ? "to the rupee" : "to the paisa"}, ${label(ROUNDING, v.day_rate_rounding.method).toLowerCase()}.` : "Not rounded.",
      ]} />;
    case "rounding":
      return <Lines items={[`${v.earnings === "gross_only" ? "Only the total" : "Each part of pay"} is rounded ${label(ROUNDING, v.method).toLowerCase()}.`]} />;
    case "pf_policy":
      return <Lines items={[
        `Employee PF on ${v.wage_base === "full" ? "all PF wages" : "PF wages up to the ceiling"}; company PF on ${v.employer_wage_base === "full" ? "all PF wages" : v.employer_wage_base === "capped" ? "PF wages up to the ceiling" : "the same"}.`,
        v.arrears_contribution === "none" ? "No PF on arrears." : "PF is also paid on arrears.",
      ]} />;
    case "esi_policy":
      return <Lines items={[`Who is covered: ${label(WAGES, v.eligibility_wage).toLowerCase()}.`, `ESI worked out on: ${label(WAGES, v.contribution_wage).toLowerCase()}.`]} />;
    case "pt_policy":
      return <Lines items={[`Slab picked from ${label(WAGES, v.basis).toLowerCase()}.`]} />;
    case "tds_policy":
      return <Lines items={[
        "Tax for the projected year, spread over the months left.",
        v.one_time_income === "tax_in_month" ? "A bonus is taxed in the month it is paid." : "Tax on a bonus is spread over the months left.",
        `Year's tax rounded to ${String(v.annual_tax_rounding_unit ?? 1) === "10" ? "the nearest ₹10" : "the rupee"}; each month's ${label(ROUNDING, v.monthly_rounding || "nearest").toLowerCase()}.`,
      ]} />;
    case "arrears_policy":
      return <Lines items={[v.enabled === false ? "Arrears are not paid." : `Arrears are paid for up to ${v.lookback_months ?? 12} months back.`]} />;
    case "salary_structure":
      return <Lines items={[(v.components || []).map((c) => `${label(COMPONENTS, c.code)} ${c.share === "remainder" ? "the rest" : pct(c.share)}`).join(" · ")]} />;
    case "work_calendar":
      return <Lines items={[
        `Days off every week: ${(v.weekly_off || []).map(cap).join(", ") || "none"}.`,
        (v.holidays || []).length ? `${v.holidays.length} holidays: ${v.holidays.join(", ")}.` : "No company holidays.",
      ]} />;
    default:
      return <DataView value={v} />;
  }
}
