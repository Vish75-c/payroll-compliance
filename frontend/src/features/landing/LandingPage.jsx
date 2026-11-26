import React, { useCallback, useEffect, useRef, useState } from "react";
import Lenis from "lenis";
import Brand from "../../shared/Brand.jsx";
import DemoChat from "./DemoChat.jsx";
import MotionHero from "./MotionHero.jsx";
import "./landing.css";
import shotOverview from "./assets/overview.jpg";
import shotMonth from "./assets/month.jpg";
import shotDues from "./assets/dues.jpg";
import shotHrData from "./assets/hrdata.jpg";
import shotRules from "./assets/rules.jpg";
import heroScene from "./assets/hero-scene.jpg";

const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------ content

const NAV = [["features", "Product"], ["how", "How a month runs"], ["ai", "Where AI stops"], ["faq", "Questions"]];

const COVERS = [
  "Provident Fund", "ESI", "Professional tax by state", "TDS, new regime", "Labour Codes wage rule", "Arrears for late raises",
  "Loss of pay", "Joiners and leavers", "Government due dates", "Finance team employee + finance head approval", "Horilla and spreadsheets", "Attendance",
];

const STATS = [
  { value: 4, label: "payment files a month", note: "PF, ESI, PT and TDS, each totalled against the register" },
  { value: 2, label: "people sign off", note: "the person who prepares a month cannot approve it" },
  { value: 0, label: "amounts worked out by AI", note: "every number comes from dated rules" },
  { value: 180, suffix: "+", label: "automated tests", note: "run on every change to the calculation" },
];

const FEATURES = [
  { id: "months", tab: "Payroll months", title: "Every month, one step at a time",
    text: "Review the HR data, add this month's changes, calculate, submit. A second person approves, and only then are the register and payment files written.",
    points: ["What changed since last month, per person", "A reason and a rule version on every amount", "Waits for the finance head through restarts"], img: shotMonth },
  { id: "hr", tab: "HR data", title: "Read your HR tool, or upload a file",
    text: "A browser agent reads employees, salary and leave from your HR tool's screens. You confirm which column means what once; later reads reuse it.",
    points: ["Horilla and other HR tools, or a spreadsheet", "Attendance suggests possible unpaid days", "Warnings must be read before the data is used"], img: shotHrData },
  { id: "dues", tab: "Government dues", title: "Know what you owe, and by when",
    text: "When a month is final, the TDS, PF, ESI and professional tax to deposit are listed with their due dates. Record the challan when you pay.",
    points: ["Due dates come from the rules the month used", "Overdue payments are flagged", "Every payment is in the month's history"], img: shotDues },
  { id: "rules", tab: "Rules & settings", title: "Rules with a start date",
    text: "Government rules and company settings are versions with a date they start from. A change is a new version, so a paid month stays explainable with the rules of its time.",
    points: ["Edited in forms, not code", "Professional tax per state", "Try a setting on a draft before using it"], img: shotRules },
  { id: "overview", tab: "Overview", title: "The whole company at a glance",
    text: "The next month to run, what is waiting for approval, who is on the rolls and which government payments are still open.",
    points: ["Several companies, one workspace", "Set up a new company step by step", "Opening balances for a mid-year start"], img: shotOverview },
];

const LEDGER = [
  { label: "Salary for July", amount: "90,000", note: "₹90,000 a month since 1 May" },
  { label: "Arrears, May and June", amount: "20,000", note: "2 × (₹90,000 − ₹80,000 already paid)", isNew: true },
  { label: "One-time bonus", amount: "10,000", note: "typed by finance, confirmed by the finance team employee", isNew: true },
  { label: "Gross pay", amount: "1,20,000", kind: "total" },
  { label: "PF", amount: "1,800", note: "12% of the ₹15,000 cap", kind: "minus" },
  { label: "Professional tax", amount: "200", note: "Karnataka slab", kind: "minus" },
  { label: "Net pay", amount: "1,18,000", kind: "net" },
];

const STEPS = [
  { title: "Read the HR tool", text: "Employees, salaries and leave from your HR tool's screens, or a spreadsheet. The field mapping is confirmed once." },
  { title: "Review what changed", text: "A late raise becomes arrears, unpaid days become a cut, a typed bonus becomes a proposed change you confirm." },
  { title: "Approve with a second person", text: "The finance team employee submits, a different person approves. The month waits for them for days if it has to." },
  { title: "Pay the government", text: "The register and one file each for PF, ESI, professional tax and TDS, checked against the register first." },
];

const AI_MAY = ["Read records from your HR tool's screens", "Suggest which HR field means which payroll field", "Turn a typed instruction into a proposed change", "Explain in plain words what changed since last month"];
const AI_NEVER = ["Work out any amount: salary, arrears, PF, ESI, PT or TDS", "Decide which rule or setting applies", "Approve, finalise or change a payroll on its own"];

const FAQ = [
  { q: "Which HR tools can it read?", a: "Horilla is profiled out of the box, and the agent has been tested on a second, deliberately different HR tool. Any tool it can sign in to works the same way: it finds the employee, salary and leave screens, and a person confirms the field mapping once. A spreadsheet upload works too." },
  { q: "What happens when a raise is approved late?", a: "The raise is recorded with the date HR entered it. Months already paid keep what was known then, and the next month pays the difference as arrears, with the calculation for each earlier month shown." },
  { q: "Can the AI change someone's salary?", a: "No. It can propose a change from a typed instruction, but an amount is used only if it appears in what was typed, and a person confirms every change. All amounts come from the calculation, never from the AI." },
  { q: "What if the approver is away for a few days?", a: "The month waits in a durable workflow. It keeps waiting through server restarts, and nothing is paid until a different person approves it." },
  { q: "Is this handling real money?", a: "No. This is a demo with made-up companies and people. No payments are made." },
];

// ------------------------------------------------------------------ motion hooks

/** Adds .is-in to [data-reveal] elements as they scroll into view. Without JS or with reduced motion, all stays visible. */
function useReveal(rootRef) {
  useEffect(() => {
    const root = rootRef.current;
    if (!root || reduceMotion() || !("IntersectionObserver" in window)) return undefined;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) { entry.target.classList.add("is-in"); observer.unobserve(entry.target); }
      }
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.1 });
    root.classList.add("lp-motion");
    root.querySelectorAll("[data-reveal]").forEach((t) => observer.observe(t));
    return () => { observer.disconnect(); root.classList.remove("lp-motion"); };
  }, [rootRef]);
}

/** Scroll position as CSS variables: --scroll (0..1 down the page) and --hero (0..1 as the hero picture comes up). */
function useScrollVars(rootRef, shotRef, setScrolled) {
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const root = rootRef.current;
      if (!root) return;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      root.style.setProperty("--scroll", max > 0 ? (window.scrollY / max).toFixed(4) : "0");
      setScrolled(window.scrollY > 16);
      const shot = shotRef.current;
      if (!reduceMotion()) root.style.setProperty("--sy", Math.min(window.scrollY, 1600).toFixed(1));
      if (shot && !reduceMotion()) {
        const top = shot.getBoundingClientRect().top;
        const progress = Math.min(1, Math.max(0, (window.innerHeight - top) / (window.innerHeight * 0.75)));
        root.style.setProperty("--hero", progress.toFixed(4));
      }
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); cancelAnimationFrame(frame); };
  }, [rootRef, shotRef, setScrolled]);
}

/** The hero scene leans toward the pointer: --mx / --my go from -1 to 1, eased every frame, nearer layers move more.
    Off for touch screens and for people who turn motion off. */
function usePointerParallax(rootRef, heroRef) {
  useEffect(() => {
    const root = rootRef.current;
    const hero = heroRef.current;
    if (!root || !hero || reduceMotion() || window.matchMedia?.("(pointer: coarse)").matches) return undefined;
    const target = { x: 0, y: 0 };
    const now = { x: 0, y: 0 };
    let frame = 0;
    const step = () => {
      now.x += (target.x - now.x) * 0.07;
      now.y += (target.y - now.y) * 0.07;
      root.style.setProperty("--mx", now.x.toFixed(4));
      root.style.setProperty("--my", now.y.toFixed(4));
      frame = Math.abs(target.x - now.x) + Math.abs(target.y - now.y) > 0.001 ? requestAnimationFrame(step) : 0;
    };
    const onMove = (e) => {
      const box = hero.getBoundingClientRect();
      if (e.clientY > box.bottom) return;
      target.x = Math.max(-1, Math.min(1, (e.clientX / window.innerWidth - 0.5) * 2));
      target.y = Math.max(-1, Math.min(1, ((e.clientY - box.top) / Math.min(box.height, window.innerHeight) - 0.5) * 2));
      if (!frame) frame = requestAnimationFrame(step);
    };
    const onLeave = () => { target.x = 0; target.y = 0; if (!frame) frame = requestAnimationFrame(step); };
    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    return () => { window.removeEventListener("pointermove", onMove); document.removeEventListener("pointerleave", onLeave); cancelAnimationFrame(frame); };
  }, [rootRef, heroRef]);
}

/** Eased, momentum-style scrolling on the landing page only. Off for people who turn motion off. */
function useSmoothScroll() {
  const lenisRef = useRef(null);
  useEffect(() => {
    if (reduceMotion()) return undefined;
    const lenis = new Lenis({ duration: 1.2, smoothWheel: true, wheelMultiplier: 1, touchMultiplier: 1 });
    lenisRef.current = lenis;
    let frame = requestAnimationFrame(function raf(time) { lenis.raf(time); frame = requestAnimationFrame(raf); });
    return () => { cancelAnimationFrame(frame); lenis.destroy(); lenisRef.current = null; };
  }, []);
  return lenisRef;
}

/** A number that counts up once when it scrolls into view. Screen readers get the final value straight away. */
function CountUp({ value, suffix = "" }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(() => (reduceMotion() ? value : 0));
  useEffect(() => {
    const el = ref.current;
    if (!el || reduceMotion() || !("IntersectionObserver" in window)) { setShown(value); return undefined; }
    let frame = 0;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / 1400);
        setShown(Math.round(value * (1 - Math.pow(1 - t, 4))));
        if (t < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    }, { threshold: 0.6 });
    observer.observe(el);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [value]);
  return <span ref={ref} aria-label={`${value}${suffix}`}><span aria-hidden="true">{shown}{suffix}</span></span>;
}

// ------------------------------------------------------------------ sections

function Header({ scrolled, entry, entryLabel, goTo }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const jump = (id) => { setOpen(false); goTo(id); };
  return (
    <header className={`lp-header${scrolled || open ? " is-scrolled" : ""}${open ? " is-open" : ""}`}>
      <div className="lp-container lp-header-row">
        <a className="lp-brand" href="#/welcome"><Brand /></a>
        <nav className="lp-nav" aria-label="Landing page">
          {NAV.map(([id, label]) => <button key={id} type="button" className="lp-navlink" onClick={() => jump(id)}>{label}</button>)}
        </nav>
        <div className="lp-header-cta">
          <a className="lp-btn lp-btn-primary lp-btn-sm" href={entry}>{entryLabel}<Arrow /></a>
          <button type="button" className="lp-burger" aria-expanded={open} aria-controls="lp-mobile-nav" aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen(!open)}><span /><span /></button>
        </div>
      </div>
      <div id="lp-mobile-nav" className="lp-mobile-nav" hidden={!open}>
        {NAV.map(([id, label], i) => <button key={id} type="button" style={{ "--i": i }} onClick={() => jump(id)}>{label}</button>)}
      </div>
    </header>
  );
}

function Arrow() {
  return <svg className="lp-arrow" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function Frame({ src, alt, eager = false }) {
  return (
    <div className="lp-frame">
      <div className="lp-frame-bar" aria-hidden="true"><i /><i /><i /><span>localhost:8080</span></div>
      <img src={src} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async" width="1440" height="900" />
    </div>
  );
}

function Marquee() {
  const row = COVERS.map((c) => <li key={c}><span className="lp-dot" aria-hidden="true" />{c}</li>);
  return (
    <section className="lp-marquee" aria-label="What every month covers">
      <p className="lp-marquee-label">Built for Indian payroll compliance</p>
      <div className="lp-marquee-mask">
        <ul className="lp-marquee-track">{row}</ul>
        <ul className="lp-marquee-track" aria-hidden="true">{row}</ul>
      </div>
    </section>
  );
}

function Features() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const tabs = useRef([]);
  const select = (i, focus = false) => { setActive(i); if (focus) tabs.current[i]?.focus(); };
  const onKey = (e) => {
    const n = FEATURES.length;
    const next = { ArrowDown: active + 1, ArrowRight: active + 1, ArrowUp: active - 1, ArrowLeft: active - 1, Home: 0, End: n - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    select((next + n) % n, true);
  };
  const f = FEATURES[active];
  return (
    <section className="lp-section" aria-labelledby="features">
      <div className="lp-container">
        <div className="lp-section-head" data-reveal>
          <p className="lp-eyebrow">The product</p>
          <h2 id="features" className="lp-h2" tabIndex={-1}>One place for the whole payroll month.</h2>
        </div>
        <div className="lp-tabs" data-reveal style={{ "--i": 1 }}
          onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
          onFocus={() => setPaused(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setPaused(false); }}>
          <div className="lp-tablist" role="tablist" aria-label="Product areas" aria-orientation="vertical" onKeyDown={onKey}>
            {FEATURES.map((x, i) => (
              <button key={x.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`tab-${x.id}`} aria-controls={`panel-${x.id}`}
                aria-selected={i === active} tabIndex={i === active ? 0 : -1} className="lp-tab" onClick={() => select(i)}>
                <span className="lp-tab-name">{x.tab}</span>
                {i === active && (
                  <span className={`lp-tab-progress${paused ? " is-paused" : ""}`} aria-hidden="true"
                    onAnimationEnd={() => setActive((active + 1) % FEATURES.length)} />
                )}
              </button>
            ))}
          </div>
          <div className="lp-tabpanel" role="tabpanel" id={`panel-${f.id}`} aria-labelledby={`tab-${f.id}`} key={f.id}>
            <div className="lp-tabpanel-copy">
              <h3>{f.title}</h3>
              <p>{f.text}</p>
              <ul>{f.points.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
            <Frame src={f.img} alt={`${f.tab} screen of the app`} />
          </div>
        </div>
      </div>
    </section>
  );
}

function Stats() {
  return (
    <section className="lp-stats" aria-label="In numbers">
      <div className="lp-container lp-stats-grid">
        {STATS.map((s, i) => (
          <div key={s.label} className="lp-stat" data-reveal style={{ "--i": i }}>
            <strong><CountUp value={s.value} suffix={s.suffix} /></strong>
            <span>{s.label}</span>
            <small>{s.note}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function Explain() {
  return (
    <section className="lp-section lp-section-alt" aria-labelledby="explain">
      <div className="lp-container lp-split">
        <div data-reveal>
          <p className="lp-eyebrow">Every number explains itself</p>
          <h2 id="explain" className="lp-h2">Click any amount and see <span className="lp-gold">why.</span></h2>
          <p className="lp-lede">Priya's raise was approved in July but started in May. July pays her the difference for May and June, and says so.</p>
        </div>
        <figure className="lp-ledger" data-reveal style={{ "--i": 1 }}>
          <figcaption><span>Brickline Design Co. · July 2025</span><b>Why Priya's pay went up</b></figcaption>
          <ol>
            {LEDGER.map((l, i) => (
              <li key={l.label} className={[l.kind, l.isNew && "new"].filter(Boolean).join(" ")} style={{ "--i": i }}>
                <span>{l.label}{l.isNew && <em>new</em>}</span>
                <span className="amt">₹{l.amount}</span>
                {l.note && <small>{l.note}</small>}
              </li>
            ))}
          </ol>
        </figure>
      </div>
    </section>
  );
}

function Steps() {
  return (
    <section className="lp-section" aria-labelledby="how">
      <div className="lp-container">
        <div className="lp-section-head" data-reveal>
          <p className="lp-eyebrow">How a month runs</p>
          <h2 id="how" className="lp-h2" tabIndex={-1}>From HR records to payment files.</h2>
          <p className="lp-lede">Run the same month twice and you get the same numbers.</p>
        </div>
        <ol className="lp-steps">
          {STEPS.map((s, i) => (
            <li key={s.title} className="lp-card lp-step" data-reveal style={{ "--i": i }}>
              <span className="lp-step-no" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function AiLimits() {
  return (
    <section className="lp-section lp-dark" aria-labelledby="ai">
      <div className="lp-container">
        <div className="lp-section-head" data-reveal>
          <p className="lp-eyebrow">Where AI stops</p>
          <h2 id="ai" className="lp-h2" tabIndex={-1}>The AI proposes. The calculation decides.</h2>
          <p className="lp-lede">Every AI answer is saved and shown to a person before it can touch a payroll.</p>
        </div>
        <div className="lp-ai">
          <div className="lp-card lp-ai-card lp-ai-may" data-reveal style={{ "--i": 0 }}>
            <h3><span aria-hidden="true">✓</span> AI may</h3>
            <ul>{AI_MAY.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
          <div className="lp-card lp-ai-card lp-ai-never" data-reveal style={{ "--i": 1 }}>
            <h3><span aria-hidden="true">✕</span> AI never</h3>
            <ul>{AI_NEVER.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
      </div>
    </section>
  );
}

function Faq() {
  const [open, setOpen] = useState(0);
  return (
    <section className="lp-section" aria-labelledby="faq">
      <div className="lp-container lp-faq-wrap">
        <div className="lp-section-head" data-reveal>
          <p className="lp-eyebrow">Questions</p>
          <h2 id="faq" className="lp-h2" tabIndex={-1}>Asked before the first month.</h2>
        </div>
        <div className="lp-faq">
          {FAQ.map((item, i) => {
            const isOpen = open === i;
            return (
              <div key={item.q} className={`lp-faq-item${isOpen ? " is-open" : ""}`} data-reveal style={{ "--i": i }}>
                <h3>
                  <button type="button" aria-expanded={isOpen} aria-controls={`faq-${i}`} id={`faq-q-${i}`} onClick={() => setOpen(isOpen ? -1 : i)}>
                    {item.q}<span className="lp-plus" aria-hidden="true" />
                  </button>
                </h3>
                <div className="lp-faq-a" id={`faq-${i}`} role="region" aria-labelledby={`faq-q-${i}`} inert={isOpen ? undefined : ""}>
                  <div><p>{item.a}</p></div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ page

export default function LandingPage({ signedIn = false }) {
  const pageRef = useRef(null);
  const shotRef = useRef(null);
  const heroRef = useRef(null);
  const [scrolled, setScrolled] = useState(false);
  useReveal(pageRef);
  useScrollVars(pageRef, shotRef, setScrolled);
  usePointerParallax(pageRef, heroRef);
  const lenisRef = useSmoothScroll();
  const entry = signedIn ? "#/" : "#/sign-in";
  const entryLabel = signedIn ? "Open workspace" : "Sign in";
  const goTo = useCallback((id) => {
    const heading = document.getElementById(id);
    if (!heading) return;
    heading.focus({ preventScroll: true });
    const y = heading.getBoundingClientRect().top + window.scrollY - 96;
    if (lenisRef.current) lenisRef.current.scrollTo(y, { duration: 1.4 });
    else window.scrollTo({ top: y, behavior: reduceMotion() ? "auto" : "smooth" });
  }, [lenisRef]);

  return (
    <div className="lp" ref={pageRef}>
      <div className="lp-progress" aria-hidden="true" />
      <Header scrolled={scrolled} entry={entry} entryLabel={entryLabel} goTo={goTo} />

      <main>
        <MotionHero ref={heroRef} entry={entry} entryLabel={entryLabel} goTo={goTo} />

        <section className="lp-hero lp-hero-next" aria-labelledby="scene">
          <div className="lp-container lp-hero-copy" data-reveal>
            <p className="lp-pill"><span className="lp-pill-dot" aria-hidden="true" />The workspace</p>
            <h2 id="scene" className="lp-h2" tabIndex={-1}>One calm place for the whole <span className="lp-gold">month.</span></h2>
            <p className="lp-hero-lede">Open July and every number shows where it came from.</p>
          </div>
          {/* Keka-style layering: the painting behind, the product in the middle, and the painting's bottom strip
              (people and grass) laid over the product again so the product stands inside the scene. */}
          <div className="lp-scene">
            <img className="lp-scene-back" src={heroScene} alt="" decoding="async" width="1376" height="768" />
            <div className="lp-scene-shot" ref={shotRef}>
              <Frame src={shotMonth} alt="The July 2025 payroll month, waiting for the finance head's approval" eager />
            </div>
            <img className="lp-scene-front" src={heroScene} alt="" aria-hidden="true" decoding="async" width="1376" height="768" />
          </div>
        </section>

        <Marquee />
        <Features />
        <Stats />
        <Explain />
        <Steps />
        <AiLimits />
        <Faq />

        <section className="lp-close" aria-labelledby="lp-close-title">
          <div className="lp-container" data-reveal>
            <h2 id="lp-close-title" className="lp-h2">Open July and see the arrears for yourself.</h2>
            <p>The demo company is ready: six people, three months already paid, and July waiting for approval.</p>
            <a className="lp-btn lp-btn-light" href={entry}>{entryLabel}<Arrow /></a>
          </div>
        </section>
      </main>

      <DemoChat goTo={goTo} />

      <footer className="lp-footer">
        <div className="lp-container lp-footer-row">
          <Brand />
          <span>Demo with made-up data. No real payments are made.</span>
          <a href={entry}>{entryLabel}</a>
        </div>
      </footer>
    </div>
  );
}
