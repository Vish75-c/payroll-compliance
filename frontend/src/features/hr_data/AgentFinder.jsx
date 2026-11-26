import React, { useEffect, useState } from "react";

// Shown while "Find screens" runs: a sketch of the agent at work in a small browser window. The cursor signs in,
// opens the menu and finds the employee, salary and leave lists. It is an illustration only: the real search runs on
// the server, and this keeps looping (after the sign-in) until the answer comes back.

const STEP_MS = 1500;
// cursor: x, y in % of the window (title bar included); click: a press when the cursor lands; screen: what the page shows.
// The window is laid out in fixed proportions (see .af-* in styles.css) so these land on the same spots at any size.
const STEPS = [
  { screen: "login", cursor: [50, 47], click: true, log: (c) => `Opening ${c.host}` },
  { screen: "login", cursor: [50, 64], click: true, log: (c) => `Signing in as ${c.user}` },
  { screen: "login", cursor: [50, 81], click: true, log: () => "Signed in" },
  { screen: "employees", cursor: [13, 27.7], click: true, log: () => "Employees: a list of people found" },
  { screen: "payroll", cursor: [13, 38.8], click: true, log: () => "Opening the Payroll menu" },
  { screen: "contracts", cursor: [17, 50], click: true, log: () => "Salary: contracts found" },
  { screen: "leave", cursor: [13, 61.1], click: true, log: () => "Opening the Leave menu" },
  { screen: "requests", cursor: [17, 72.2], click: true, log: () => "Leave: leave requests found" },
  { screen: "requests", cursor: [68, 84], click: false, log: () => "Suggesting the screens to read…" },
];
const LOOP_FROM = 3; // after signing in once, keep browsing the menu

const MENU = [
  { id: "dashboard", label: "Dashboard" },
  { id: "employees", label: "Employees" },
  { id: "payroll", label: "Payroll" },
  { id: "contracts", label: "Contracts", sub: true },
  { id: "leave", label: "Leave" },
  { id: "requests", label: "Requests", sub: true },
  { id: "reports", label: "Reports" },
];
const TITLES = { employees: "Employees", payroll: "Payroll", contracts: "Contracts", leave: "Leave", requests: "Leave requests" };
const FOUND = new Set(["employees", "contracts", "requests"]);

const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export default function AgentFinder({ baseUrl, username }) {
  const still = reduceMotion();
  const [step, setStep] = useState(still ? 3 : 0);
  const [done, setDone] = useState(still ? 3 : 0); // log lines shown so far (grows to the full list, then stays)
  useEffect(() => {
    if (still) return undefined;
    const t = setInterval(() => {
      setStep((s) => (s + 1 >= STEPS.length ? LOOP_FROM : s + 1));
      setDone((d) => Math.min(d + 1, STEPS.length - 1));
    }, STEP_MS);
    return () => clearInterval(t);
  }, [still]);

  let host = baseUrl || "the HR tool";
  try { host = new URL(baseUrl).host; } catch { /* keep as typed */ }
  const ctx = { host, user: username || "the saved login" };
  const s = STEPS[step];
  const app = s.screen !== "login";
  const loginField = step; // 0: username typed, 1: password typed, 2: button pressed

  return (
    <div className="af" role="status" aria-live="polite" aria-label="The agent is looking through the HR tool">
      <div className="af-window" aria-hidden="true">
        <div className="af-bar">
          <span className="af-dots"><i /><i /><i /></span>
          <span className="af-url"><span className="af-lock">🔒</span>{host}{app ? `/${s.screen === "requests" ? "leave/requests" : s.screen}` : "/login"}</span>
        </div>

        {!app ? (
          <div className="af-login">
            <div className="af-login-box">
              <b>Sign in</b>
              <span className={`af-input${loginField >= 0 ? " is-filled" : ""}`}><em className="af-type">{ctx.user}</em></span>
              <span className={`af-input${loginField >= 1 ? " is-filled" : ""}`}><em className="af-type">••••••••</em></span>
              <span className={`af-btn${loginField >= 2 ? " is-pressed" : ""}`}>Sign in</span>
            </div>
          </div>
        ) : (
          <div className="af-app">
            <nav className="af-menu">
              {MENU.map((m) => (
                <span key={m.id} className={`af-menu-item${m.sub ? " is-sub" : ""}${s.screen === m.id ? " is-active" : ""}${FOUND.has(m.id) && STEPS.findIndex((x) => x.screen === m.id) < done ? " is-found" : ""}`}>
                  {m.label}
                </span>
              ))}
            </nav>
            <div className="af-page" key={s.screen}>
              <div className="af-page-title">{TITLES[s.screen]}</div>
              {FOUND.has(s.screen) ? (
                <div className="af-table">
                  {Array.from({ length: 6 }, (_, i) => <span key={i} className="af-row" style={{ "--i": i }}><i /><i /><i /><i /></span>)}
                  <span className="af-scan" />
                  <span className="af-found">✓ list found</span>
                </div>
              ) : (
                <div className="af-cards">{Array.from({ length: 4 }, (_, i) => <span key={i} style={{ "--i": i }} />)}</div>
              )}
            </div>
          </div>
        )}

        {!still && (
          <span className="af-cursor" style={{ left: `${s.cursor[0]}%`, top: `${s.cursor[1]}%` }}>
            <svg viewBox="0 0 20 20"><path d="M3 2 L3 16 L7 12.5 L9.6 18 L12 17 L9.5 11.6 L15 11.6 Z" /></svg>
            {s.click && <span className="af-click" key={step} />}
            <span className="af-tag">Agent</span>
          </span>
        )}
      </div>

      <ol className="af-log">
        {STEPS.slice(0, done + 1).slice(-4).map((x, i, shown) => {
          const current = i === shown.length - 1;
          return <li key={x.log(ctx)} className={current ? "is-now" : ""}><span className="af-tick" aria-hidden="true">{current ? "" : "✓"}</span>{x.log(ctx)}</li>;
        })}
      </ol>
    </div>
  );
}
