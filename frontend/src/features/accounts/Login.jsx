import React, { useRef, useState } from "react";
import { api } from "../../shared/api.js";
import Brand from "../../shared/Brand.jsx";
import { Banner, Field } from "../../shared/ui.jsx";

// Demo accounts only: the seeded finance team employee and finance head. One click fills the form.
const DEMO_ACCOUNTS = [
  { username: "maker", password: "maker123", label: "Finance team employee", initials: "FT", role: "prepares the month" },
  { username: "checker", password: "checker123", label: "Finance head", initials: "FH", role: "approves it" },
];

export default function Login({ onDone, notice }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filled, setFilled] = useState({ who: "", n: 0 });
  // "Add a company": after signing in, go straight to setting up a new company (finance team employee only).
  const [addCompany, setAddCompany] = useState(false);
  const submitRef = useRef(null);
  const fill = (account) => {
    setUsername(account.username);
    setPassword(account.password);
    setError("");
    setFilled((f) => ({ who: account.username, n: f.n + 1 }));
    submitRef.current?.focus();
  };
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      onDone(await api.signIn(username, password), addCompany ? "/onboarding" : undefined);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="public-page login-page">
      <div className="login-rings" aria-hidden="true"><i /><i /><i /><b /></div>
      <header className="login-top">
        <a className="public-brand-link" href="#/welcome"><Brand /></a>
        <a className="login-back" href="#/welcome"><span aria-hidden="true">←</span> Back to home</a>
      </header>
      <div className="login-layout">
        <aside className="login-context" aria-label="Payroll approval">
          <p className="login-kicker"><span aria-hidden="true" />Payroll for Indian companies</p>
          <h2>Two people sign off <em>every</em> month.</h2>
          <div className="login-cards" aria-hidden="true">
            <div className="login-card login-card-dark"><b>TDS</b><span>₹12,567</span></div>
            <div className="login-card login-card-green"><b>PF</b><span>₹18,480</span></div>
            <div className="login-card login-card-gold"><b>PT</b><span>₹800</span></div>
          </div>
        </aside>
        <form className="login-form" onSubmit={submit}>
          <div>
            <h1>{addCompany ? "Add a company" : "Sign in"}</h1>
            <p>{addCompany ? "Sign in as a finance team employee. You'll go straight to setting up the new company." : "Open your payroll workspace."}</p>
          </div>
          {notice && <Banner>{notice}</Banner>}
          {error && <div role="alert"><Banner kind="bad">{error}</Banner></div>}
          <Field label="Username"><input key={`u${filled.n}`} className={filled.n ? "just-filled" : undefined} autoFocus={!filled.n} autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
          <Field label="Password"><input key={`p${filled.n}`} className={filled.n ? "just-filled" : undefined} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <button ref={submitRef} className="public-button public-button-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : addCompany ? "Sign in and add a company" : "Sign in"}</button>
          <button type="button" className={`login-add-company${addCompany ? " is-on" : ""}`} aria-pressed={addCompany} onClick={() => setAddCompany((v) => !v)}>
            {addCompany ? <><span aria-hidden="true">←</span> Just sign in</> : <><span aria-hidden="true">+</span> Add a company</>}
          </button>
          <div className="login-demo" role="group" aria-label="Demo accounts">
            <div className="login-demo-buttons">
              {DEMO_ACCOUNTS.map((a) => (
                <button key={a.username} type="button" className={`login-demo-btn${filled.who === a.username ? " is-picked" : ""}`}
                  aria-pressed={filled.who === a.username} onClick={() => fill(a)}>
                  <span className="login-demo-avatar" aria-hidden="true">{a.initials}</span>
                  <span><b>{a.label}</b><small>{a.username} · {a.role}</small></span>
                  <span className="login-demo-tick" aria-hidden="true">✓</span>
                </button>
              ))}
            </div>
          </div>
        </form>
      </div>
    </main>
  );
}
