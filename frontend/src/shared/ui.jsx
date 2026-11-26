import React, { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------- formatting
const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
export function inr(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return (n < 0 ? "−₹" : "₹") + inrFmt.format(Math.abs(n));
}
export function signed(value) {
  const n = Number(value || 0);
  if (n === 0) return "no change";
  return (n > 0 ? "+" : "−") + "₹" + inrFmt.format(Math.abs(n));
}
export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function day(iso) {
  if (!iso) return "—";
  return new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}
export function monthLabel(period) {
  if (!period) return "";
  const [y, m] = period.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

// ---------------------------------------------------------------- hooks
export function useLoad(loader, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const alive = useRef(true);
  const reload = useCallback(async (quiet) => {
    if (!quiet) setState((s) => ({ ...s, loading: true }));
    try {
      const data = await loader();
      if (alive.current) setState({ data, error: null, loading: false });
      return data;
    } catch (e) {
      if (alive.current) setState((s) => ({ ...s, error: e.message, loading: false }));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    alive.current = true;
    reload();
    return () => { alive.current = false; };
  }, [reload]);
  return { ...state, reload };
}

export function usePoll(fn, active, ms = 1200) {
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(fn, ms);
    return () => clearInterval(t);
  }, [fn, active, ms]);
}

// ---------------------------------------------------------------- toasts
let pushToast = () => {};
export function toast(message, kind = "info") { pushToast({ message, kind, id: Math.random() }); }
export function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    pushToast = (t) => {
      setItems((xs) => [...xs, t]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), t.kind === "bad" ? 9000 : 4500);
    };
  }, []);
  return (
    <div className="toast">
      {items.map((t) => (
        <Banner key={t.id} kind={t.kind}>{t.message}</Banner>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- a small burst of confetti on success
// Starts where the person last pressed (the button they clicked), about 14 pieces, under a second. Nothing for
// people who turn motion off.
let lastPoint = null;
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", (e) => { lastPoint = { x: e.clientX, y: e.clientY, at: Date.now() }; }, true);
}
const CONFETTI_COLOURS = ["#1f5a43", "#2f8a5f", "#15be53", "#d9b06a", "#f4e2bb", "#9db2ff"];
export function celebrate(x, y) {
  if (typeof document === "undefined" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const recent = lastPoint && Date.now() - lastPoint.at < 15000;
  const ox = x ?? (recent ? lastPoint.x : window.innerWidth - 120);
  const oy = y ?? (recent ? lastPoint.y : window.innerHeight - 80);
  const layer = document.createElement("div");
  layer.className = "confetti";
  layer.setAttribute("aria-hidden", "true");
  document.body.appendChild(layer);
  const pieces = 14;
  for (let i = 0; i < pieces; i++) {
    const el = document.createElement("i");
    el.style.background = CONFETTI_COLOURS[i % CONFETTI_COLOURS.length];
    el.style.left = `${ox}px`;
    el.style.top = `${oy}px`;
    if (i % 3 === 0) el.style.borderRadius = "50%";
    layer.appendChild(el);
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.1;
    const dist = 40 + Math.random() * 46;
    const dx = Math.cos(angle) * dist;
    const dy = Math.sin(angle) * dist;
    el.animate([
      { transform: "translate(-50%, -50%) scale(.6) rotate(0deg)", opacity: 1 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(1) rotate(${Math.random() * 300}deg)`, opacity: 1, offset: 0.45 },
      { transform: `translate(calc(-50% + ${dx * 1.15}px), calc(-50% + ${dy + 46}px)) scale(.8) rotate(${Math.random() * 540}deg)`, opacity: 0 },
    ], { duration: 820 + Math.random() * 260, easing: "cubic-bezier(.22, 1, .36, 1)", fill: "forwards" });
  }
  setTimeout(() => layer.remove(), 1300);
}

// Run an async action with a busy flag and an error toast.
export function useAction() {
  const [busy, setBusy] = useState(null);
  const run = useCallback(async (name, fn, okMessage) => {
    setBusy(name);
    try {
      const out = await fn();
      if (okMessage) { toast(okMessage, "ok"); celebrate(); }
      return out;
    } catch (e) {
      toast(e.message, "bad");
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  return [busy, run];
}

// ---------------------------------------------------------------- components
const ICONS = { info: "ℹ", ok: "✓", warn: "!", bad: "✕", ai: "✦" };
export function Banner({ kind = "info", title, children, action }) {
  return (
    <div className={`banner ${kind}`}>
      <span className="icon">{ICONS[kind]}</span>
      <div style={{ flex: 1 }}>
        {title && <strong>{title} </strong>}
        {children}
      </div>
      {action}
    </div>
  );
}

const STATUS_KIND = {
  draft: "", calculated: "info", awaiting_approval: "warn", rejected: "bad", approved: "ok", finalized: "ok", cancelled: "",
  pending_review: "warn", approved_snapshot: "ok", ready: "info", needs_mapping: "warn", running: "info", queued: "info",
  done: "ok", failed: "bad", accepted: "ok", informational: "info", error: "bad",
};
export function StatusChip({ status, label }) {
  return <span className={`chip ${STATUS_KIND[status] ?? ""}`}>{label || status}</span>;
}

export const isModel = (provider) => Boolean(provider && provider !== "rule-based");
export function aiName(provider) {
  return isModel(provider) ? "Model-assisted" : "Rule-based";
}

export function AIChip({ provider }) {
  if (isModel(provider)) return <span className="chip ai">✦ {aiName(provider)}</span>;
  return <span className="chip" title="No AI model configured: a rule-based matcher made this suggestion">Rule-based</span>;
}

export function Spinner() { return <span className="spinner" aria-label="loading" />; }

export function Loading({ error }) {
  if (error) return <Banner kind="bad">{error}</Banner>;
  return <div className="empty"><Spinner /> Loading…</div>;
}

export function Field({ label, children, hint }) {
  return (
    <label className="field">
      {label}
      {children}
      {hint && <span className="muted small" style={{ fontWeight: 400 }}>{hint}</span>}
    </label>
  );
}

// A form that opens in front of the page, wherever the button that opened it was, with the cursor in its
// first field. Escape or the ✕ closes it; a click outside does not, so nothing typed is lost by accident.
export function Modal({ title, onClose, children }) {
  const panel = useRef(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => { // once, when it opens: typing must not move the cursor back to the first field
    const before = document.activeElement;
    const onKey = (e) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    const scroll = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const first = panel.current?.querySelector("input[type=date], input:not([disabled]), select, textarea");
    first?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = scroll;
      before?.focus?.();
    };
  }, []);
  return (
    <div className="modal-back">
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={panel}>
        <header>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="body">{children}</div>
      </div>
    </div>
  );
}

export function Drawer({ title, subtitle, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-back" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <header>
          <div>
            <h2 style={{ margin: 0 }}>{title}</h2>
            {subtitle && <div className="muted small">{subtitle}</div>}
          </div>
          <button onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="body">{children}</div>
      </aside>
    </>
  );
}

export function Checks({ checks }) {
  if (!checks || !checks.length) return <p className="muted">No checks.</p>;
  const order = { error: 0, warning: 1, ok: 2, info: 3 };
  // Long lists (85 employees can give many checks) scroll inside their card; errors and warnings come first.
  return (
    <div className="checks-scroll" tabIndex={0} aria-label="Checks">
      {[...checks].sort((a, b) => (order[a.level] ?? 9) - (order[b.level] ?? 9)).map((c, i) => (
        <div className="check" key={i}>
          <span className={`dot ${c.level}`} />
          <span>{c.message}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- long lists
// Search, filters and a count above a table, so 80 or 800 rows stay usable.
export const matches = (query, ...fields) => {
  const q = (query || "").trim().toLowerCase();
  return !q || fields.some((f) => String(f ?? "").toLowerCase().includes(q));
};
export function TableToolbar({ query, onQuery, placeholder = "Search by name or ID", shown, total, noun = "rows", children }) {
  return (
    <div className="toolbar">
      <input type="search" value={query} placeholder={placeholder} onChange={(e) => onQuery(e.target.value)} aria-label={placeholder} />
      {children}
      <span className="spacer" />
      <span className="muted small">{shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`}</span>
    </div>
  );
}
// A small set of mutually exclusive filters shown as buttons: [[value, label, count?], ...]
export function Segmented({ value, onChange, options }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, label, count]) => (
        <button key={v} type="button" className={value === v ? "on" : ""} onClick={() => onChange(v)}>
          {label}{count !== undefined && <span className="seg-count">{count}</span>}
        </button>
      ))}
    </div>
  );
}
export function Select({ value, onChange, options, label }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}
// State codes as people say them.
export const STATE_NAMES = { KA: "Karnataka", MH: "Maharashtra", TN: "Tamil Nadu", TS: "Telangana", AP: "Andhra Pradesh", DL: "Delhi",
  GJ: "Gujarat", WB: "West Bengal", KL: "Kerala", UP: "Uttar Pradesh", RJ: "Rajasthan", HR: "Haryana", MP: "Madhya Pradesh" };
export const stateName = (code) => STATE_NAMES[code] || code;

// Horizontal bars for a few counts (departments, states): [[label, count], ...]
export function Bars({ items }) {
  const max = Math.max(1, ...items.map(([, n]) => n));
  return (
    <div className="bars">
      {items.map(([label, n]) => (
        <div key={label} className="bar-row">
          <span className="bar-label">{label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(n / max) * 100}%` }} /></span>
          <span className="bar-n">{n}</span>
        </div>
      ))}
    </div>
  );
}

export function Json({ value }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

// Any data shown as words and tables instead of JSON: keys become labels ("max_rebate" -> "Max rebate"),
// a list of records becomes a table, a list of plain values a comma-separated line.
const words = (key) => String(key).replace(/[_.@]+/g, " ").replace(/^./, (c) => c.toUpperCase());
function plain(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}
export function DataView({ value }) {
  if (value === null || value === undefined || typeof value !== "object") return <span>{plain(value)}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every((x) => x === null || typeof x !== "object")) return <span>{value.map(plain).join(", ")}</span>;
    const cols = [...new Set(value.flatMap((x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.keys(x) : [])))];
    if (!cols.length) return <ul className="summary">{value.map((x, i) => <li key={i}><DataView value={x} /></li>)}</ul>;
    return (
      <div className="table-wrap"><table className="mini">
        <thead><tr>{cols.map((c) => <th key={c}>{words(c)}</th>)}</tr></thead>
        <tbody>{value.map((x, i) => <tr key={i}>{cols.map((c) => <td key={c}><DataView value={x?.[c]} /></td>)}</tr>)}</tbody>
      </table></div>
    );
  }
  const entries = Object.entries(value);
  if (!entries.length) return <span className="muted">none</span>;
  return (
    <dl className="dataview">
      {entries.map(([k, v]) => (
        <React.Fragment key={k}><dt>{words(k)}</dt><dd><DataView value={v} /></dd></React.Fragment>
      ))}
    </dl>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([key, label]) => (
        <button key={key} role="tab" className={value === key ? "on" : ""} onClick={() => onChange(key)}>{label}</button>
      ))}
    </div>
  );
}

export function Stat({ label, value, delta, hint }) {
  const n = Number(delta || 0);
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && (
        <div className={`delta ${n > 0 ? "up" : n < 0 ? "down" : "muted"}`}>{signed(delta)}{hint ? ` ${hint}` : ""}</div>
      )}
    </div>
  );
}
