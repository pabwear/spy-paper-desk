// trade.riosventures.org: the desk's dashboard behind a password.
// The page is the same one as the Claude artifact; here it reads the desk's numbers from /data/<file>,
// which this Worker serves from KV (the desk copies them there after every run: site_publish.py).
// Sign-in: one password (the SITE_PASSWORD secret), checked here, never in the page. A signed cookie
// keeps you signed in for 30 days; changing the password signs everyone out.
import PAGE from "./dist/index.html";

const COOKIE = "desk_session";
const DAYS = 30;
const FILE_RE = /^[A-Za-z0-9_.-]{1,80}\.json$/;
const HEADERS = {
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex, nofollow",
  "Strict-Transport-Security": "max-age=31536000",
};

const enc = new TextEncoder();
const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function hmacKey(password) {
  const seed = await crypto.subtle.digest("SHA-256", enc.encode("spy-desk-session:" + password));
  return crypto.subtle.importKey("raw", seed, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}
async function sign(password, text) {
  return b64url(await crypto.subtle.sign("HMAC", await hmacKey(password), enc.encode(text)));
}
function sameText(a, b) { // constant time for equal lengths
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
async function signedIn(request, password) {
  const m = (request.headers.get("Cookie") || "").match(new RegExp(`(?:^|;\\s*)${COOKIE}=(\\d+)\\.([A-Za-z0-9_-]+)`));
  if (!m || Number(m[1]) < Date.now()) return false;
  return sameText(m[2], await sign(password, "until:" + m[1]));
}
async function passwordOk(given, password) { // compare digests so the time doesn't depend on the text
  const [a, b] = await Promise.all([given, password].map((t) => crypto.subtle.digest("SHA-256", enc.encode(t))));
  return sameText(b64url(a), b64url(b));
}

function html(body, status = 200, extra = {}) {
  return new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...HEADERS, ...extra } });
}
function loginPage(wrong = false) {
  return html(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kura Trades · sign in</title><meta name="robots" content="noindex">
<style>:root{color-scheme:dark;--bg:#0b0d16;--card:#141826;--ink:#e8eaf2;--muted:#9aa0b4;--line:#262b3d;--accent:#f0a07a;--bad:#ff8a8a}
@media (prefers-color-scheme: light){:root{color-scheme:light;--bg:#f5f6fa;--card:#fff;--ink:#141826;--muted:#5b6175;--line:#dfe2ea;--accent:#c4643a;--bad:#c0392b}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}
form{width:100%;max-width:360px;box-sizing:border-box;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:28px}
h1{margin:0 0 4px;font-size:22px}p{margin:0 0 20px;color:var(--muted);font-size:14px}
input{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);font-size:16px}
button{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#141826;font-weight:600;font-size:16px}
.bad{color:var(--bad);margin:12px 0 0;font-size:14px}</style></head><body>
<form method="post" action="/login"><h1>Kura Trades</h1><p>Paper trading desk · private</p>
<input type="password" name="password" autocomplete="current-password" placeholder="Password" required autofocus>
<button type="submit">Sign in</button>${wrong ? '<p class="bad">That password didn\'t match.</p>' : ""}</form></body></html>`, wrong ? 401 : 200);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const password = env.SITE_PASSWORD;
    if (!password) return html("<p>This site isn't set up yet (no password).</p>", 503);

    if (url.pathname === "/login") {
      if (request.method === "POST") {
        const form = await request.formData().catch(() => null);
        const given = form ? String(form.get("password") || "") : "";
        if (!(await passwordOk(given, password))) {
          await new Promise((r) => setTimeout(r, 1500)); // slow down guessing
          return loginPage(true);
        }
        const until = Date.now() + DAYS * 86400000;
        const cookie = `${COOKIE}=${until}.${await sign(password, "until:" + until)}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`;
        return new Response(null, { status: 303, headers: { Location: "/", "Set-Cookie": cookie, ...HEADERS } });
      }
      return loginPage();
    }
    if (url.pathname === "/logout") {
      return new Response(null, { status: 303, headers: { Location: "/login", "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`, ...HEADERS } });
    }

    if (!(await signedIn(request, password))) {
      if (url.pathname.startsWith("/data/")) return new Response("sign in", { status: 401, headers: { "Cache-Control": "no-store", ...HEADERS } });
      return new Response(null, { status: 303, headers: { Location: "/login", ...HEADERS } });
    }

    if (url.pathname.startsWith("/data/")) {
      const name = decodeURIComponent(url.pathname.slice(6));
      if (!FILE_RE.test(name)) return new Response("not found", { status: 404, headers: HEADERS });
      const body = await env.DESK.get(name);
      if (body === null) return new Response("not found", { status: 404, headers: { "Cache-Control": "no-store", ...HEADERS } });
      return new Response(body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...HEADERS } });
    }
    if (url.pathname === "/" || url.pathname === "/index.html") return html(PAGE);
    return new Response("not found", { status: 404, headers: HEADERS });
  },
};
