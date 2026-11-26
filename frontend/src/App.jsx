import React, { useEffect, useState } from "react";
import { api } from "./shared/api.js";
import { go } from "./shared/routing.js";
import { Banner, Loading, Toasts, useAction } from "./shared/ui.jsx";
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
import HelpPage, { setHelpUser } from "./shared/Help.jsx";

// How each role is named where people read it (the role codes stay maker / checker).
const ROLE_NAMES = { maker: "Finance team employee", checker: "Finance head" };

// The monthly work is two steps in order: bring in the HR data, then run the payroll month.
// Those two items carry a step number instead of an icon so the order reads at a glance.
const NAV = [
  ["/", "Overview", "◎"],
  ["/data", "HR data", "⇩", 1],
  ["/months", "Payroll months", "▦", 2],
  ["/employees", "Employees", "☷"],
  ["/dues", "Government dues", "⌛"],
  ["/rules", "Rules & settings", "§"],
  ["/ai", "AI log", "✦"],
  ["/help", "Help", "?"],
];

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
  setHelpUser(me.user.id);  // page tips are remembered per person
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
  else if (parts[0] === "rules") page = <Rules company={company} me={me} />;
  else if (parts[0] === "employees") page = <EmployeesPage company={company} me={me} />;
  else if (parts[0] === "help") page = <HelpPage />;
  else if (parts[0] === "ai") page = <AILog company={company} />;
  else page = <Banner kind="warn">Page not found.</Banner>;

  const isChecker = me.roles.includes("checker");
  // Government dues: the finance head only. HR data: the finance team only (the finance head sees a month's data from its page).
  const nav = NAV.filter(([path]) => (path !== "/dues" || isChecker) && (path !== "/data" || canUseHrData));
  const numbered = nav.some(([path]) => path === "/data");
  const active = (path) => (path === "/" ? route === "/" : route.startsWith(path) || (path === "/months" && route.startsWith("/runs")));
  return (
    <div className="shell">
      <aside className="side">
        {/* The product name, with the company being worked on right under it (a switcher when there are several). */}
        <div className="side-brand">
          <a className="side-brand-mark product-mark has-logo" href="#/" aria-label={`${PRODUCT_NAME} home`}><img src={brandMark} alt="" /></a>
          <div className="side-brand-text">
            <a className="side-brand-name" href="#/">{PRODUCT_NAME}</a>
            {company && (companies.length > 1
              ? <select className="side-company" aria-label="Company" value={company.id} onChange={(e) => switchTo(e.target.value)}>
                  {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              : <span className="side-company" title={company.name}>{company.name}</span>)}
          </div>
        </div>
        {company && <nav className="workspace-nav" aria-label="Payroll workspace">{nav.map(([path, label, icon, step]) => {
          // Number the steps only when step 1 is in the menu (the finance head has no HR data page).
          const n = step && numbered ? step : null;
          return (
            <a key={path} href={"#" + path} aria-current={active(path) ? "page" : undefined} className={active(path) ? "active" : ""}>
              {n ? <span className="nav-step" aria-hidden="true">{n}</span> : <span aria-hidden="true">{icon}</span>}
              {n && <span className="sr-only">Step {n}: </span>}{label}
            </a>
          );
        })}</nav>}
        <div className="foot">
          <strong className="workspace-user">{(me.user.name || "").replace(/\s*\([^)]*\)\s*$/, "")}</strong>
          <div>{me.roles.length ? me.roles.map((r) => ROLE_NAMES[r] || r).join(" & ") : "no role"}</div>
          <button className="workspace-signout" disabled={!!busy} onClick={() => act("sign-out", signOut)}>{busy ? "Signing out…" : "Sign out"}</button>
        </div>
      </aside>
      <main className="main">{page}</main>
      <Toasts />
    </div>
  );
}
