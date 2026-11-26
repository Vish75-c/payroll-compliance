import React from "react";
import { go, ws } from "../../shared/routing.js";

export default function HrmsDirectory({ companies }) {
  return (
    <div>
      <h1>Your HRMS</h1>
      {companies.map((c) => <button key={c.id} onClick={() => go(ws("/", c.id))}>{c.name}</button>)}
    </div>
  );
}
