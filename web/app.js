"use strict";
// Display-only number formatting lives here; every protocol calculation happens server-side in integers.
const $ = (id) => document.getElementById(id);
let csrf = "", state = null, timer = null;

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const k of kids) n.append(k);
  return n;
}
const svg = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

const fmt = (micro, dp = 2) => (micro / 1e6).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
const pct = (bps) => (bps === null ? "no debt" : (bps / 100).toFixed(1) + "%");

function toMicro(text) {
  const m = /^(\d{1,7})(?:\.(\d{1,6}))?$/.exec(text.trim());
  if (!m) return null;
  return Number(m[1]) * 1e6 + Number((m[2] || "").padEnd(6, "0"));
}

function say(text, ok = false) {
  const m = $("msg");
  m.textContent = text;
  m.className = ok ? "ok" : "err";
}

async function api(path, method = "GET", body) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-CSRF-Token"] = csrf;
    opts.body = JSON.stringify(body || {});
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "request rejected (check the values you entered)");
  }
  return data;
}

async function act(fn, done) {
  try {
    await fn();
    if (done) say(done, true);
    await refresh();
  } catch (e) {
    say(e.message);
  }
}

// ---- auth ----------------------------------------------------------------

async function enter(data) {
  csrf = data.csrf;
  $("auth").hidden = true;
  $("app").hidden = false;
  $("who").hidden = false;
  $("whoName").textContent = data.username;
  say("");
  await refresh();
}

async function auth(kind) {
  const f = $("authForm");
  if (!f.reportValidity()) return;
  try {
    await enter(await api("/api/" + kind, "POST", { username: $("user").value, password: $("pass").value }));
    $("pass").value = "";
  } catch (e) {
    say(e.message);
  }
}

$("authForm").addEventListener("submit", (e) => { e.preventDefault(); auth("login"); });
$("registerBtn").addEventListener("click", () => auth("register"));
$("logout").addEventListener("click", () => act(async () => {
  await api("/api/logout", "POST");
  location.reload();
}));

// ---- rendering -----------------------------------------------------------

function point(f, r) {
  const a = Math.PI * (1 - f);
  return [100 + r * Math.cos(a), 100 - r * Math.sin(a)];
}

function gauge(bps, p) {
  const MAX = 300;
  const g = svg("svg", { class: "gauge", viewBox: "0 0 200 120", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(MAX),
    "aria-valuenow": bps === null ? "0" : String(Math.min(MAX, Math.round(bps / 100))),
    "aria-label": "Collateral ratio " + pct(bps) });
  const cut = [0, p.liq_bps / 100 / MAX, p.mint_bps / 100 / MAX, 1];
  ["red", "amber", "green"].forEach((c, i) => {
    const [x0, y0] = point(cut[i], 80), [x1, y1] = point(cut[i + 1], 80);
    g.append(svg("path", { class: "seg " + c, d: `M${x0} ${y0} A80 80 0 0 1 ${x1} ${y1}` }));
  });
  if (bps !== null) {
    const [nx, ny] = point(Math.min(1, bps / 100 / MAX), 62);
    g.append(svg("line", { class: "needle", x1: "100", y1: "100", x2: String(nx), y2: String(ny) }));
  }
  const t = svg("text", { x: "100", y: "118" });
  t.textContent = pct(bps);
  g.append(t);
  return g;
}

function renderMarket() {
  const s = state;
  $("clock").textContent = `hour ${s.tick} · scenario: ${s.scenario}`;
  $("pMarket").textContent = "$" + fmt(s.market);
  $("pOracle").textContent = "$" + fmt(s.oracle);
  $("blurb").textContent = s.scenarios[s.scenario];
  const box = $("scenarios");
  box.replaceChildren();
  for (const name of Object.keys(s.scenarios)) {
    const r = el("input", { type: "radio", name: "scn", value: name });
    r.checked = name === s.scenario;
    r.addEventListener("change", () => act(() => api("/api/sim/scenario", "POST", { name }), "Scenario switched"));
    box.append(el("label", {}, r, name));
  }
  const h = s.history, all = h.flatMap((x) => [x.market, x.oracle]);
  const lo = Math.min(...all), hi = Math.max(...all), span = Math.max(1, hi - lo);
  const line = (key, cls) => {
    const pts = h.map((x, i) => `${h.length < 2 ? 0 : (i * 300) / (h.length - 1)},${65 - ((x[key] - lo) * 60) / span}`);
    return svg("polyline", { class: cls, points: pts.join(" ") });
  };
  $("spark").replaceChildren(line("market", "m"), line("oracle", "o"));
}

function renderWallet() {
  const s = state, w = s.wallet, p = s.params;
  const rows = (id, list) => {
    const dl = $(id);
    dl.replaceChildren();
    for (const [k, v] of list) dl.append(el("dt", {}, k), el("dd", {}, v));
  };
  rows("wallet", [["COL", fmt(w.col)], ["FORGE", fmt(w.forge)], ["USDC", fmt(w.usdc)]]);
  rows("system", [
    ["FORGE peg", "$" + fmt(s.peg, 4)],
    ["Stability fee", (p.fee_bps_year / 100).toFixed(2) + "% / yr (index " + (s.rate / 1e12).toFixed(6) + ")"],
    ["PSM fee in / out", (p.tin_bps / 100).toFixed(2) + "% / " + (p.tout_bps / 100).toFixed(2) + "%"],
    ["PSM USDC reserve", fmt(s.psm_usdc)],
    ["Total debt", fmt(s.total_debt)],
    ["Surplus", fmt(s.surplus)],
    ["Bad debt", fmt(s.bad_debt)],
  ]);
}

function renderVaults(vaults, preview) {
  const box = $("vaults"), p = state.params;
  box.replaceChildren();
  if (!vaults.length) box.append(el("p", { class: "muted" }, "No open vaults. Open one, deposit COL, then mint FORGE."));
  for (const v of vaults) {
    const pv = preview && preview.vaults.find((x) => x.id === v.id);
    const shown = pv || v;
    const card = el("article", { class: "card" + (shown.liquidatable ? " risk" : "") });
    card.append(el("h3", {}, "Vault #" + v.id));
    card.append(gauge(shown.ratio_bps, p));
    if (pv) card.append(el("p", { class: "muted" }, `At $${fmt(preview.price)}: ${shown.liquidatable ? "would be liquidated" : "safe"}`));
    card.append(el("p", {}, `Collateral ${fmt(v.ink)} COL · debt ${fmt(v.debt)} FORGE`));
    card.append(el("p", { class: "muted" }, `Can mint ${fmt(v.max_mint)} more`));
    const amt = el("input", { inputmode: "decimal", placeholder: "amount", "aria-label": "Amount for vault " + v.id });
    const row = el("div", { class: "row" });
    for (const op of ["deposit", "withdraw", "mint", "repay", "close"]) {
      row.append(el("button", { type: "submit", "data-op": op, class: op === "close" ? "ghost" : "" }, op));
    }
    const f = el("form", {}, amt, row);
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      const op = e.submitter.dataset.op;
      const micro = op === "close" ? 0 : toMicro(amt.value);
      if (micro === null || (op !== "close" && micro < 1)) return say("Enter a positive amount with up to 6 decimals.");
      act(() => api(`/api/vaults/${v.id}/${op}`, "POST", { amount: micro }), op + " done");
    });
    card.append(f);
    box.append(card);
  }
}

function renderLog(rows) {
  const body = $("log");
  body.replaceChildren();
  $("logEmpty").hidden = rows.length > 0;
  for (const a of rows) {
    const cells = [a.id, "#" + a.vault_id + (a.mine ? " (you)" : ""), "h" + a.start_tick, fmt(a.lot), fmt(a.debt),
      a.status === "active" ? "live @ $" + fmt(a.price_now) : "settled h" + a.end_tick,
      a.price === null ? "-" : "$" + fmt(a.price), a.paid === null ? "-" : fmt(a.paid),
      a.bad_debt === null ? "-" : fmt(a.bad_debt), a.keeper_profit === null ? "-" : fmt(a.keeper_profit)];
    body.append(el("tr", {}, ...cells.map((c) => el("td", {}, String(c)))));
  }
}

async function refresh() {
  const shock = Number($("shock").value);
  const [s, vaults, log, pv] = await Promise.all([
    api("/api/state"), api("/api/vaults"), api("/api/auctions"),
    shock ? api("/api/preview?shock_bps=" + shock * 100) : Promise.resolve(null),
  ]);
  state = s;
  renderMarket();
  renderWallet();
  renderVaults(vaults, pv);
  renderLog(log);
}

// ---- controls ------------------------------------------------------------

document.querySelectorAll("[data-hours]").forEach((b) =>
  b.addEventListener("click", () => act(() => api("/api/sim/advance", "POST", { hours: Number(b.dataset.hours) }))));
$("openVault").addEventListener("click", () => act(() => api("/api/vaults", "POST"), "Vault opened"));
$("shock").addEventListener("input", () => {
  const v = Number($("shock").value);
  $("shockVal").textContent = (v > 0 ? "+" : "") + v + "%";
  clearTimeout(timer);
  timer = setTimeout(() => act(async () => {}), 150);
});
$("psmForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const micro = toMicro($("psmAmt").value);
  if (micro === null || micro < 1) return say("Enter a positive amount with up to 6 decimals.");
  act(() => api("/api/psm", "POST", { side: e.submitter.dataset.side, amount: micro }), "Swap done");
});

// Resume an existing session if the cookie is still good.
api("/api/me").then(enter).catch(() => {});
