import React, { useEffect, useRef, useState } from "react";

// A demo assistant for the landing page. Answers are prewritten and picked by keywords: no AI, nothing is sent anywhere.

const SUGGESTIONS = ["How are arrears worked out?", "Can AI change a salary?", "Which HR tools work?", "How do I try the demo?"];

const ANSWERS = [
  { keys: ["hi", "hello", "hey", "namaste", "good morning", "good evening"],
    text: "Hello! I can tell you how a payroll month works here: arrears, unpaid leave, government dues, approvals and where AI is used. What would you like to know?" },
  { keys: ["arrear", "late raise", "raise", "increment", "backdated", "back dated", "revision"],
    text: "When HR approves a raise late, months already paid keep what was known at the time. The next month pays the difference as arrears. Example: Priya's raise from ₹80,000 to ₹90,000 started in May but was approved in July, so July pays 2 × ₹10,000 = ₹20,000 extra, with the working shown for each month.",
    jump: ["explain", "See the example"] },
  { keys: ["unpaid", "lop", "loss of pay", "leave", "absence", "absent", "attendance"],
    text: "Unpaid leave comes from the HR tool's leave records. If you upload daily attendance, working days with no check-in and no approved leave are suggested as possible unpaid days. A person adds or dismisses each one with a reason, so nothing cuts pay on its own." },
  { keys: ["pf", "esi", "professional tax", " pt", "tds", "due", "challan", "government", "deposit"],
    text: "When a month is final, the TDS, PF, ESI and professional tax to deposit are listed with their due dates, which come from the rules that month used. You record the challan number when you pay, and late payments are flagged." },
  { keys: ["hr tool", "horilla", "hrms", "spreadsheet", "excel", "upload", "import", "integration"],
    text: "A browser agent reads employees, salary and leave from your HR tool's screens (Horilla is set up already), or you upload a spreadsheet. You confirm once which column means what; later reads reuse that.",
    jump: ["features", "See the HR data screen"] },
  { keys: ["ai", "artificial", "model", "claude", "gpt", "llm"],
    text: "AI only suggests: it can read HR screens, suggest field mappings, turn a typed instruction into a proposed change and explain what changed. It never works out an amount, picks a rule or approves anything. An amount is used only if you typed it.",
    jump: ["ai", "Where AI stops"] },
  { keys: ["approve", "approval", "maker", "checker", "four eyes", "second person", "sign off"],
    text: "Two people sign off every month. The finance team employee prepares and submits it; a different person, the finance head, approves. The month waits for them, even through server restarts, and the payment files are written only after approval." },
  { keys: ["onboard", "new company", "add a company", "set up", "setup", "opening balance", "mid year", "mid-year"],
    text: "A new company is set up step by step: details and registration numbers, pay settings, HR data, opening balances (pay and TDS earlier in the year) and then its first month. You can stop and pick up again at any step." },
  { keys: ["demo", "try", "login", "log in", "sign in", "password", "credential", "account"],
    text: "Sign in and choose a demo account: Finance team employee (maker / maker123) prepares the month, Finance head (checker / checker123) approves it. On the sign-in page one click fills them in.",
    link: ["#/sign-in", "Go to sign in"] },
  { keys: ["price", "pricing", "cost", "plan", "subscription", "free"],
    text: "This is a demo with made-up companies and people, so there is no pricing and no real payments are made." },
  { keys: ["thank", "thanks", "great", "cool", "nice"],
    text: "Glad to help! Anything else about the payroll month?" },
];

const FALLBACK = "I'm a demo assistant with prewritten answers, so I may not know that one. Try asking about arrears, unpaid leave, government dues, approvals, HR tools or AI.";

function answerFor(question) {
  // A keyword counts when a word starts with it ("arrear" matches "arrears"; "ai" does not match "unpaid").
  const q = question.toLowerCase();
  const hit = (k) => new RegExp(`\\b${k.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(q);
  let best = null;
  let bestScore = 0;
  for (const a of ANSWERS) {
    const score = a.keys.reduce((n, k) => n + (hit(k) ? k.trim().length : 0), 0);
    if (score > bestScore) { best = a; bestScore = score; }
  }
  return best || { text: FALLBACK, suggest: true };
}

let nextId = 1;

export default function DemoChat({ goTo }) {
  const [open, setOpen] = useState(false);
  const [teaser, setTeaser] = useState(false);
  const [messages, setMessages] = useState([{ id: nextId++, from: "bot", text: "Hi! I'm the payroll demo assistant. Ask me how a payroll month works, or pick a question below." }]);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const [unread, setUnread] = useState(1);
  const inputRef = useRef(null);
  const launcherRef = useRef(null);
  const listRef = useRef(null);
  const timers = useRef([]);

  // A small greeting bubble appears a few seconds after the page opens, unless the chat was already opened.
  useEffect(() => {
    const t = setTimeout(() => setTeaser(true), 4000);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  useEffect(() => {
    if (!open) return undefined;
    setTeaser(false);
    setUnread(0);
    const t = setTimeout(() => inputRef.current?.focus(), 250);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => { clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [open]);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
  }, [messages, typing]);

  const close = () => { setOpen(false); launcherRef.current?.focus(); };

  const ask = (text) => {
    const question = text.trim();
    if (!question || typing) return;
    setMessages((m) => [...m, { id: nextId++, from: "me", text: question }]);
    setDraft("");
    setTyping(true);
    const reply = answerFor(question);
    const delay = Math.min(1600, 600 + reply.text.length * 4);
    timers.current.push(setTimeout(() => {
      setTyping(false);
      setMessages((m) => [...m, { id: nextId++, from: "bot", ...reply }]);
    }, delay));
  };

  const lastBot = [...messages].reverse().find((m) => m.from === "bot");
  const showChips = messages.length === 1 || lastBot?.suggest;

  return (
    <div className={`lp-chat${open ? " is-open" : ""}`}>
      {teaser && !open && (
        <div className="lp-chat-teaser" role="status">
          <button type="button" className="lp-chat-teaser-close" aria-label="Hide message" onClick={() => setTeaser(false)}>×</button>
          <button type="button" className="lp-chat-teaser-body" onClick={() => setOpen(true)}>
            <b>Questions about payroll?</b>
            <span>Ask the demo assistant.</span>
          </button>
        </div>
      )}

      <section className="lp-chat-panel" role="dialog" aria-label="Payroll demo assistant" aria-modal="false" hidden={!open}>
        <header className="lp-chat-head">
          <span className="lp-chat-avatar" aria-hidden="true">₹<i /></span>
          <div>
            <b>Payroll assistant</b>
            <small>Demo · prewritten answers, no AI</small>
          </div>
          <button type="button" className="lp-chat-x" aria-label="Close chat" onClick={close}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          </button>
        </header>

        <div className="lp-chat-list" ref={listRef} aria-live="polite" data-lenis-prevent>
          {messages.map((m) => (
            <div key={m.id} className={`lp-msg lp-msg-${m.from}`}>
              <p>{m.text}</p>
              {(m.jump || m.link) && (
                <div className="lp-msg-actions">
                  {m.jump && <button type="button" onClick={() => { close(); goTo(m.jump[0]); }}>{m.jump[1]} →</button>}
                  {m.link && <a href={m.link[0]}>{m.link[1]} →</a>}
                </div>
              )}
            </div>
          ))}
          {typing && <div className="lp-msg lp-msg-bot lp-typing" aria-label="The assistant is typing"><i /><i /><i /></div>}
          {showChips && !typing && (
            <div className="lp-chips">
              {SUGGESTIONS.map((s, i) => <button key={s} type="button" style={{ "--i": i }} onClick={() => ask(s)}>{s}</button>)}
            </div>
          )}
        </div>

        <form className="lp-chat-form" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
          <input ref={inputRef} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask about arrears, dues, approvals…" aria-label="Your question" maxLength={300} />
          <button type="submit" aria-label="Send" disabled={!draft.trim() || typing}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8h10M8.5 3.5 13 8l-4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
        </form>
      </section>

      <button ref={launcherRef} type="button" className="lp-chat-launcher" aria-expanded={open} aria-label={open ? "Close chat" : "Open chat"}
        onClick={() => (open ? close() : setOpen(true))}>
        <svg className="lp-chat-icon-open" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="M8.5 9.5h.01M12 9.5h.01M15.5 9.5h.01" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
        <svg className="lp-chat-icon-close" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        {unread > 0 && !open && <span className="lp-chat-badge" aria-hidden="true">{unread}</span>}
      </button>
    </div>
  );
}
