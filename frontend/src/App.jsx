import React, { useEffect, useState } from "react";
import { api } from "./shared/api.js";
import { go } from "./shared/routing.js";
import { Banner, Loading, Toasts, stateName, useAction } from "./shared/ui.jsx";
import Brand, { PRODUCT_NAME, brandMark } from "./shared/Brand.jsx";
import SessionGate from "./features/accounts/SessionGate.jsx";
import Overview from "./features/companies/Overview.jsx";
import { DataSources, JobPage, SnapshotPage } from "./features/hr_data/HRData.jsx";
import Months from "./features/payroll_runs/Months.jsx";
import RunPage from "./features/payroll_runs/Run.jsx";
import Rules from "./features/rules/Rules.jsx";
import AILog from "./features/ai/AILog.jsx";
import Dues from "./features/dues/Dues.jsx";
import Onboarding from "./features/onboarding/Onboarding.jsx";
import EmployeesPage from "./features/pay_groups/PayGroups.jsx";

// How each role is named where people read it (the role codes stay maker / checker).
const ROLE_NAMES = { maker: "Finance team employee", checker: "Finance head" };

const NAV = [
  ["/", "Overview", "◎"],
  ["/data", "HR data", "⇩"],
  ["/months", "Payroll months", "▦"],
  ["/dues", "Government dues", "⌛"],
  ["/rules", "Rules & settings", "§"],
  ["/ai", "AI log", "✦"],
];

// The company being worked on, as a card under the product name. With several companies it opens a short list.
function CompanySwitcher({ company, companies, onPick }) {
  const [open, setOpen] = useState(false);
  const many = companies.length > 1;
  const place = company.city || stateName(company.state) || "";
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!e.target.closest?.(".side-company")) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const body = (
    <>
      <span className="side-company-avatar" aria-hidden="true">{company.name.slice(0, 1).toUpperCase()}</span>
      <span className="side-company-text"><b title={company.name}>{company.name}</b>{place && <small>{place}</small>}</span>
      {many && <span className="side-company-chev" aria-hidden="true">⌄</span>}
    </>
  );
  return (
    <div className={`side-company${open ? " is-open" : ""}`}>
      {many ? <button type="button" className="side-company-btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>{body}</button>
        : <div className="side-company-btn is-static">{body}</div>}
      {open && (
        <ul className="side-company-menu" role="listbox" aria-label="Switch company">
          {companies.map((c) => (
            <li key={c.id}><button type="button" role="option" aria-selected={c.id === company.id} className={c.id === company.id ? "is-on" : ""}
              onClick={() => { setOpen(false); if (c.id !== company.id) onPick(String(c.id)); }}>
              <span>{c.name}</span>{c.id === company.id && <span aria-hidden="true">✓</span>}
            </button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

export default function App() {
  return (
    <SessionGate>
      {(me, signOut, route) => <PayrollApp key={me.user.id} me={me} signOut={signOut} route={route} />}
    </SessionGate>
  );
}

function rememberedCompany() {
  try { return Number(window.localStorage.getItem("company")) || null; } catch { return null; }
}

function PayrollApp({ me, signOut, route }) {
  const [companies, setCompanies] = useState(null);
  const [companyId, setCompanyId] = useState(rememberedCompany);
  const [error, setError] = useState("");
  const [busy, act] = useAction();
  const pick = (id) => { setCompanyId(id); try { window.localStorage.setItem("company", String(id)); } catch { /* not remembered */ } };

  useEffect(() => {
    let active = true;
    api.get("/companies")
      .then((list) => { if (active) setCompanies(list); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  const parts = route.split("/").filter(Boolean);
  // Setting up a company switches to it.
  const wanted = parts[0] === "onboarding" && parts[1] ? Number(parts[1]) : null;
  useEffect(() => { if (wanted && wanted !== companyId) pick(wanted); }, [wanted]);

  const company = companies ? companies.find((c) => c.id === (wanted || companyId)) || companies[0] || null : null;
  const created = (c) => { setCompanies([...companies, c]); pick(c.id); go(`/onboarding/${c.id}`); };
  const changed = (c) => setCompanies(companies.map((x) => (x.id === c.id ? { ...x, ...c } : x)));
  const switchTo = (value) => {
    pick(Number(value));
    go("/");
  };
  const onboarding = <Onboarding companyId={wanted} me={me} onCompanyCreated={created} onCompanyChanged={changed} />;

  const canUseHrData = me.roles.includes("maker");
  let page;
  if (error) page = <Banner kind="bad" title="Cannot reach the server.">{error}</Banner>;
  else if (companies === null) page = <Loading />;
  else if (!company || parts[0] === "onboarding") page = onboarding;
  else if (route === "/" || route === "/sign-in") page = <Overview company={company} me={me} />;
  else if (parts[0] === "months") page = <Months company={company} me={me} />;
  // A month's HR data, opened from that month: it stays under Payroll, and its back link returns to the month.
  else if (parts[0] === "runs" && parts[1] && parts[2] === "data" && parts[3]) page = (
    <SnapshotPage key={`${parts[1]}-${parts[3]}`} snapshotId={Number(parts[3])} me={me}
      back={{ href: `#/runs/${parts[1]}`, label: "Back to the month" }} />
  );
  else if (parts[0] === "runs" && parts[1]) page = <RunPage key={parts[1]} runId={Number(parts[1])} me={me} />;
  else if (parts[0] === "data" && parts[1] !== "snapshot" && !canUseHrData) page = <Banner kind="warn">HR data is handled by the finance team. You can see the data each month used from that month's page.</Banner>;
  else if (parts[0] === "data" && parts[1] === "job") page = <JobPage key={parts[2]} jobId={Number(parts[2])} me={me} />;
  else if (parts[0] === "data" && parts[1] === "snapshot") page = <SnapshotPage key={parts[2]} snapshotId={Number(parts[2])} me={me} />;
  else if (parts[0] === "data") page = <DataSources company={company} me={me} />;
  else if (parts[0] === "dues") page = me.roles.includes("checker") ? <Dues company={company} me={me} /> : <Banner kind="warn">Government dues are shown to the finance head only.</Banner>;
  // Moving people between pay groups by hand: reached from Rules & settings, only when there is more than one group.
  else if (parts[0] === "rules" && parts[1] === "pay-groups") page = <EmployeesPage company={company} me={me} />;
  else if (parts[0] === "rules") page = <Rules company={company} me={me} />;
  else if (parts[0] === "ai") page = <AILog company={company} />;
  else page = <Banner kind="warn">Page not found.</Banner>;

  const isChecker = me.roles.includes("checker");
  // Government dues: the finance head only. HR data: the finance team only (the finance head sees a month's data from its page).
  const displayName = (me.user.name || me.user.username || "").replace(/\s*\([^)]*\)\s*$/, "");
  const nav = NAV.filter(([path]) => (path !== "/dues" || isChecker) && (path !== "/data" || canUseHrData));
  const active = (path) => (path === "/" ? route === "/" : route.startsWith(path) || (path === "/months" && route.startsWith("/runs")));
  return (
    <div className="shell">
      <aside className="side">
        {/* The product name, with the company being worked on right under it (a switcher when there are several). */}
        <a className="side-brand" href="#/" aria-label={`${PRODUCT_NAME} home`}>
          <span className="side-brand-mark product-mark has-logo" aria-hidden="true"><img src={brandMark} alt="" /></span>
          <span className="side-brand-name">{PRODUCT_NAME}</span>
        </a>
        {company && <CompanySwitcher company={company} companies={companies} onPick={switchTo} />}
        {company && <nav className="workspace-nav" aria-label="Payroll workspace">{nav.map(([path, label, icon]) => (
          <a key={path} href={"#" + path} aria-current={active(path) ? "page" : undefined} className={active(path) ? "active" : ""}>
            <span aria-hidden="true">{icon}</span>{label}
          </a>
        ))}</nav>}
        <div className="foot">
          <div className="side-user">
            <span className="side-user-avatar" aria-hidden="true">{initials(displayName)}</span>
            <span className="side-user-text">
              <b className="workspace-user" title={displayName}>{displayName}</b>
              <small>{me.roles.length ? me.roles.map((r) => ROLE_NAMES[r] || r).join(" & ") : "no role"}</small>
            </span>
            <button className="workspace-signout" disabled={!!busy} onClick={() => act("sign-out", signOut)}
              aria-label="Sign out" title="Sign out">
              {busy ? <span className="side-user-busy" aria-hidden="true" /> : (
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M16 16l4-4-4-4M20 12H10" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
              )}
            </button>
          </div>
        </div>
      </aside>
      <main className="main">{page}</main>
      <Toasts />
    </div>
  );
}
