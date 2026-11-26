import React, { forwardRef } from "react";
import { HrmsTile, PayrollTile, Roamers } from "./HeroAgents.jsx";
import mark from "../../shared/assets/prosperr-mark.png";

// The home page's first screen: a 3D payslip folder whose payment-file cards rise out, fan open and slide back in,
// on soft concentric rings, your HRMS on the left and payroll fields on the right. Two agent cursors roam the screen
// and stop on these to read them (see HeroAgents.jsx). The folder's motion is CSS on an 8-second cycle.

const CARDS = [
  // fan: where each card settles when the folder opens (x, y, depth in px; tilt in degrees)
  { id: "tds", name: "TDS", full: "Income tax", amount: "₹12,567", tone: "dark", fan: [-22, -204, 60, -6] },
  { id: "pf", name: "PF", full: "Provident Fund", amount: "₹18,480", tone: "green", fan: [84, -142, 80, 8] },
  { id: "esi", name: "ESI", full: "State insurance", amount: "₹1,561", tone: "silver", fan: [-92, -100, 100, -12] },
  { id: "pt", name: "PT", full: "Professional tax", amount: "₹800", tone: "gold", fan: [40, -58, 120, 5] },
];

// Background sparkles: fixed positions so the page looks the same on every load (left %, size px, delay s, duration s).
const SPARKS = [[8, 3, 0, 14], [17, 2, 6, 18], [26, 4, 2.5, 16], [35, 2, 9, 20], [44, 3, 4, 15], [53, 2, 11, 19],
  [61, 4, 1, 17], [69, 2, 7.5, 21], [77, 3, 3.5, 16], [85, 2, 10, 18], [92, 3, 5.5, 15], [4, 2, 12.5, 22]];

function Arrow() {
  return <svg className="lp-arrow" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

const MotionHero = forwardRef(function MotionHero({ entry, entryLabel, goTo }, ref) {
  return (
    <section className="lp-mhero" aria-labelledby="lp-title" ref={ref}>
      {/* background: slow glows, a dot grid that drifts, and sparkles rising past the stage */}
      <div className="lp-mhero-bg" aria-hidden="true">
        <span className="lp-glow lp-glow-1" /><span className="lp-glow lp-glow-2" /><span className="lp-glow lp-glow-3" />
        <span className="lp-dotgrid" />
        {SPARKS.map(([x, size, delay, dur], i) => (
          <span key={i} className="lp-spark" style={{ "--x": `${x}%`, "--size": `${size}px`, "--delay": `${delay}s`, "--dur": `${dur}s` }} />
        ))}
      </div>
      <div className="lp-mhero-grid">
        {/* left: the message, the actions, and your HRMS (where the reading agent works) */}
        <div className="lp-mhero-copy">
          <p className="lp-mhero-brand lp-enter" style={{ "--d": "0ms" }}><span aria-hidden="true" />Payroll for Indian companies</p>
          <h1 id="lp-title" className="lp-mhero-title lp-enter-h1">Payroll you can <span className="lp-gold">explain.</span></h1>
          <p className="lp-mhero-lede lp-enter-soft">
            Late raises, unpaid leave and one-off bonuses, worked out from dated rules. Nothing is final until a second person approves it.
          </p>
          <div className="lp-mhero-actions lp-enter-soft" style={{ "--d": "700ms" }}>
            <a className="lp-btn lp-btn-dark" href={entry}>{entryLabel}<Arrow /></a>
            <button className="lp-btn lp-btn-outline" type="button" onClick={() => goTo("how")}>How a month runs</button>
          </div>

          <div className="lp-enter-soft" style={{ "--d": "900ms" }}><HrmsTile /></div>
        </div>

        {/* centre: the 3D payslip folder */}
        <div className="lp-stage" aria-hidden="true">
          <div className="lp-rings"><i /><i /><i /><b className="lp-orbit" /><b className="lp-orbit lp-orbit-inner" /><span className="lp-ripple" /><span className="lp-ripple" /></div>
          <div className="lp-stage-3d">
            <div className="lp-folder-back" />
            <div className="lp-folder-lid"><span>July 2025 · payment files</span></div>
            {CARDS.map((c, i) => (
              <div key={c.id} className={`lp-paycard lp-paycard-${c.tone}`} data-roam="map" data-roam-says={`mapping ${c.name} → ${c.full}|${c.full}: ${c.amount}`} style={{ "--k": i, "--fx": `${c.fan[0]}px`, "--fy": `${c.fan[1]}px`, "--fz": `${c.fan[2]}px`, "--fr": `${c.fan[3]}deg` }}>
                <div className="lp-paycard-top"><b>{c.name}</b><span className="lp-paycard-mark">₹</span></div>
                <div className="lp-paycard-chip" />
                <div className="lp-paycard-foot"><span>{c.full}</span><b>{c.amount}</b></div>
              </div>
            ))}
            <div className="lp-folder-front" data-roam="read" data-roam-says="handing over 220 rows|filing July 2025 data">
              <span className="lp-folder-band" />
              <span className="lp-folder-logo"><i className="has-logo"><img src={mark} alt="" /></i> Prosperr.io</span>
            </div>
          </div>
          <div className="lp-stage-shadow" />
        </div>

        {/* right: payroll fields (where the mapping agent works) */}
        <div className="lp-mhero-side lp-enter" style={{ "--d": "600ms" }}>
          <PayrollTile />
        </div>
      </div>

      <Roamers />

      <div className="lp-mhero-foot">
        <button type="button" className="lp-play" onClick={() => goTo("features")}><span aria-hidden="true">▶</span>See the product</button>
      </div>
    </section>
  );
});

export default MotionHero;
