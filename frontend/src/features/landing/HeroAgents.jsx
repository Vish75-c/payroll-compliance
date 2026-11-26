import React, { useEffect, useRef, useState } from "react";

// The hero's agents: two cursors that roam the first screen and stop on things to read them.
//   Data reading agent  → your HRMS (reads its lists) and the payslip folder (hands the rows over)
//   Field mapping agent → the payment-file cards and the payroll-fields tile (names what each column means)
// Each move picks a random target and a random spot on it, so the path never repeats. Targets are any element in the
// hero with data-roam="read" or data-roam="map"; data-roam-says lists what the agent says there ("|"-separated).

function HrmsLogo() {
  // A generic HR system mark (a person on an ID card), not any vendor's logo.
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <rect x="4" y="7" width="32" height="26" rx="6" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <circle cx="15" cy="17" r="3.6" fill="currentColor" />
      <path d="M9 27c.8-3.6 3.2-5.4 6-5.4s5.2 1.8 6 5.4" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M24 16h7M24 21h7M24 26h4" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

function PayrollLogo() {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <rect x="6" y="5" width="28" height="30" rx="6" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <text x="20" y="27" textAnchor="middle" fontSize="17" fontWeight="700" fill="currentColor">₹</text>
    </svg>
  );
}

/** Left: where the data comes from. */
export function HrmsTile() {
  return (
    <figure className="lp-tile" data-roam="read" data-roam-says="reading Employees|reading Salary|reading Leave|reading 80 rows">
      <span className="lp-tile-icon"><HrmsLogo /></span>
      <figcaption><b>Your HRMS</b><small>read by the agent, like a person would</small></figcaption>
    </figure>
  );
}

/** Right: where it ends up. */
export function PayrollTile() {
  return (
    <figure className="lp-tile is-end" data-roam="map" data-roam-says="Basic → Basic pay|Date Joined → Joining date|LOP Days → Unpaid days|Work Location → PT state">
      <span className="lp-tile-icon"><PayrollLogo /></span>
      <figcaption><b>Payroll fields</b><small>each column mapped, then a person confirms</small></figcaption>
    </figure>
  );
}

const AGENTS = [
  { id: "read", name: "Data reading agent", tone: "green", start: [0.12, 0.78] },
  { id: "map", name: "Field mapping agent", tone: "ink", start: [0.86, 0.5] },
];
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (xs) => xs[Math.floor(Math.random() * xs.length)];
const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function useRoam(layerRef, id, start) {
  const [at, setAt] = useState({ x: 0, y: 0, ms: 0, says: "", reading: false, ready: false });
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return undefined;
    const hero = layer.parentElement;
    let timer = 0;
    let here = null;
    const place = (fx, fy) => {
      const box = layer.getBoundingClientRect();
      return { x: box.width * fx, y: box.height * fy };
    };
    const first = place(...start);
    here = first;
    setAt({ ...first, ms: 0, says: "", reading: false, ready: true });
    if (reduceMotion()) return undefined;

    const step = () => {
      if (document.hidden) { timer = setTimeout(step, 1000); return; }
      const targets = [...hero.querySelectorAll(`[data-roam="${id}"]`)].filter((el) => el.getBoundingClientRect().width > 0);
      if (!targets.length) { timer = setTimeout(step, 1500); return; }
      const el = pick(targets);
      const box = layer.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const to = { x: r.left - box.left + r.width * rand(0.25, 0.75), y: r.top - box.top + r.height * rand(0.3, 0.7) };
      const dist = Math.hypot(to.x - here.x, to.y - here.y);
      const ms = Math.round(Math.min(2200, 700 + dist * 1.6));
      here = to;
      const says = pick((el.dataset.roamSays || "reading").split("|"));
      setAt({ ...to, ms, says, reading: false, ready: true });
      // arrive, read for a moment, then move on
      timer = setTimeout(() => {
        setAt((a) => ({ ...a, reading: true }));
        timer = setTimeout(step, rand(1300, 2300));
      }, ms);
    };
    timer = setTimeout(step, id === "map" ? 1600 : 700);
    return () => clearTimeout(timer);
  }, [layerRef, id, start]);
  return at;
}

function Roamer({ layerRef, agent }) {
  const at = useRoam(layerRef, agent.id, agent.start);
  if (!at.ready) return null;
  return (
    <span className={`lp-roamer is-${agent.tone}${at.reading ? " is-reading" : ""}`}
      style={{ transform: `translate3d(${at.x}px, ${at.y}px, 0)`, transitionDuration: `${at.ms}ms` }}>
      <svg className="lp-roamer-arrow" viewBox="0 0 24 24"><path d="M4.5 3.2 19 10.4c.9.45.82 1.76-.13 2.1l-5.86 2.07-2.3 5.7c-.37.93-1.7.9-2.04-.04L3.2 4.6c-.3-.86.5-1.7 1.3-1.4Z" /></svg>
      <span className="lp-roamer-tag">{agent.name}</span>
      {at.reading && at.says && <span className="lp-roamer-says" key={`${at.x}-${at.y}`}>{at.says}<i /><i /><i /></span>}
      {at.reading && <span className="lp-roamer-ping" key={`p-${at.x}-${at.y}`} />}
    </span>
  );
}

/** The layer the two agents move on: covers the whole hero, never takes clicks. */
export function Roamers() {
  const layerRef = useRef(null);
  return (
    <div className="lp-roam" ref={layerRef} aria-hidden="true">
      {AGENTS.map((a) => <Roamer key={a.id} layerRef={layerRef} agent={a} />)}
    </div>
  );
}
