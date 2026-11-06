"use strict";
/* PiBoard web terminal. xterm.js <-> WebSocket <-> PiBoard <-> kiosk/hostd.py (pty). */

const $ = (s) => document.querySelector(s);
const LS = (k, d) => { try { const v = localStorage.getItem("piboard." + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const enc = new TextEncoder();

let term = null, fit = null, ws = null, ctrl = false;

function say(msg) { $("#term-msg").textContent = msg; }
function setLive(on, text) {
  $("#term-dot").classList.toggle("on", on);
  $("#term-host").textContent = text;
  $("#term-end").classList.toggle("hidden", !on);
}
function showAuth(msg = "") {
  $("#term-wrap").classList.add("hidden"); $("#term-keys").classList.add("hidden");
  $("#term-auth").classList.remove("hidden");
  $("#term-go").disabled = false; $("#term-go").textContent = term ? "Reconnect" : "Open terminal";
  say(msg); setTimeout(() => $("#term-pw").focus(), 50);
}

async function init() {
  // never on the Pi's own display (and the server refuses it anyway)
  if (LS("kiosk", false)) return lockout("The terminal isn't available on the Pi display. Open it from your phone or laptop.");
  let st = {};
  try { st = await (await fetch("/api/status", { credentials: "same-origin" })).json(); } catch { /* offline */ }
  if (st.role !== "admin") return lockout("Sign in to PiBoard as admin first, then open the terminal again.");
  try { const s = await (await fetch("/api/stats")).json(); $("#term-host").textContent = `Terminal on ${s.hostname}`; document.title = `Terminal · ${s.hostname}`; }
  catch { /* keep the default title */ }
  showAuth();
}
function lockout(msg) {
  $("#term-pw").classList.add("hidden"); $("#term-go").classList.add("hidden");
  say(msg);
}

$("#term-auth").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#term-go").disabled = true; $("#term-go").textContent = "Checking…"; say("");
  try {
    const r = await fetch("/api/terminal/ticket", { method: "POST", credentials: "same-origin",
      headers: { "X-Piboard": "1", "Content-Type": "application/json" }, body: JSON.stringify({ password: $("#term-pw").value }) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    $("#term-pw").value = "";
    connect(data.ticket);
  } catch (err) { showAuth(err.message); }
});

function makeTerm() {
  term = new Terminal({
    fontFamily: 'ui-monospace, "DejaVu Sans Mono", "Cascadia Mono", Menlo, monospace',
    fontSize: matchMedia("(max-width: 600px)").matches ? 12 : 14, lineHeight: 1.15, cursorBlink: true,
    scrollback: 5000, allowProposedApi: false, allowTransparency: true, macOptionIsMeta: true,
    theme: { background: "rgba(0,0,0,0)", foreground: "#e6e9ef", cursor: "#4fd1c5", cursorAccent: "#062523",
      selectionBackground: "rgba(79,209,197,.32)", black: "#1b2130", brightBlack: "#5b6478", red: "#f56565", brightRed: "#ff8a8a",
      green: "#48bb78", brightGreen: "#6fd39b", yellow: "#ecc94b", brightYellow: "#f6dd7a", blue: "#63a4ff", brightBlue: "#8fbfff",
      magenta: "#b794f4", brightMagenta: "#d0b4ff", cyan: "#4fd1c5", brightCyan: "#81e6d9", white: "#d5dae4", brightWhite: "#ffffff" },
  });
  fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("#term"));
  term.onData((d) => {
    if (ctrl && d.length === 1) {                 // sticky Ctrl from the key bar: Ctrl+letter
      const c = d.toUpperCase().charCodeAt(0);
      if (c >= 64 && c <= 95) d = String.fromCharCode(c - 64);
      armCtrl(false);
    }
    send({ d });
  });
  term.onResize(({ cols, rows }) => send({ r: [cols, rows] }));
  new ResizeObserver(() => { try { fit.fit(); } catch { /* hidden */ } }).observe($("#term-wrap"));
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function connect(ticket) {
  $("#term-auth").classList.add("hidden");
  $("#term-wrap").classList.remove("hidden"); $("#term-keys").classList.remove("hidden");
  if (!term) makeTerm(); else term.reset();
  fit.fit(); term.focus();
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    ws.send(JSON.stringify({ ticket, cols: term.cols, rows: term.rows }));
    setLive(true, $("#term-host").textContent.replace(/ \(ended\)$/, ""));
  };
  ws.onmessage = (e) => term.write(typeof e.data === "string" ? enc.encode(e.data) : new Uint8Array(e.data));
  ws.onclose = (e) => {
    ws = null;
    setLive(false, $("#term-host").textContent.replace(/ \(ended\)$/, "") + " (ended)");
    term.write("\r\n\x1b[2m[Session ended]\x1b[0m\r\n");
    showAuth(e.code === 1008 ? "Your sign-in for the terminal expired. Enter the password again." : "The terminal closed. Enter the password to open a new one.");
  };
}
$("#term-end").addEventListener("click", () => { if (ws) ws.close(); });

// extra keys for phones
const KEYS = { esc: "\x1b", tab: "\t", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D" };
function armCtrl(on) { ctrl = on; $("#k-ctrl").classList.toggle("armed", on); }
document.querySelectorAll("#term-keys button").forEach((b) => {
  b.addEventListener("pointerdown", (e) => e.preventDefault());   // keep the phone keyboard open
  b.addEventListener("click", () => {
    const k = b.dataset.k;
    if (k === "ctrl") armCtrl(!ctrl);
    else send({ d: KEYS[k] || k });
    if (term) term.focus();
  });
});
window.addEventListener("beforeunload", () => { if (ws) ws.close(); });

init();
