// Small fetch wrapper: session cookie + CSRF token, JSON in and out, readable errors.
const sessionExpired = "payroll:session-expired";
let sessionRevision = 0;

export function onSessionExpired(listener) {
  window.addEventListener(sessionExpired, listener);
  return () => window.removeEventListener(sessionExpired, listener);
}

function cookie(name) {
  const m = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[2]) : "";
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(method, url, body, isForm) {
  const revision = sessionRevision;
  const headers = { "X-CSRFToken": cookie("payroll_csrftoken") };
  let payload;
  if (isForm) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch("/api" + url, { method, headers, body: payload, credentials: "same-origin", cache: "no-store" });
  if (res.status === 401 && revision === sessionRevision) {
    sessionRevision += 1;
    window.dispatchEvent(new Event(sessionExpired));
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = (data && data.detail) || (typeof data === "string" && data.slice(0, 200)) || res.statusText;
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body = {}) => request("POST", url, body),
  patch: (url, body = {}) => request("PATCH", url, body),
  del: (url) => request("DELETE", url),
  upload: (url, formData) => request("POST", url, formData, true),
  session: () => request("GET", "/me"),
  signIn: async (username, password) => {
    await request("GET", "/csrf");
    const session = await request("POST", "/login", { username, password });
    sessionRevision += 1;
    return session;
  },
  signOut: async () => {
    await request("POST", "/logout", {});
    sessionRevision += 1;
  },
};
