"use strict";
/* PiBoard frontend - vanilla JS, no build step. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const LS = {
  get(k, d) { try { const v = localStorage.getItem("piboard." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("piboard." + k, JSON.stringify(v)); } catch { /* ignore */ } },
};
// Kiosk display (touch-only): hide the mouse cursor. Set before boot() rewrites
// the /kiosk URL; the localStorage flag covers reloads on the kiosk.
if (location.pathname === "/kiosk" || LS.get("kiosk", false)) document.documentElement.classList.add("no-cursor");

const state = { role: null, settings: null, view: "dash", timers: {}, stats: null };

/* ---------------- helpers ---------------- */
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Piboard": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && !opts.auth) { showAuth(); throw new Error("Signed out"); }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v; // only used with static SVG strings
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
let toastT;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastT); toastT = setTimeout(() => (t.className = "toast"), 3000);
}
const fmtBytes = (b) => { const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return (i >= 3 ? b.toFixed(1) : Math.round(b)) + " " + u[i]; };
const fmtUptime = (s) => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; };

const ICONS = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>',
  restart: '<svg viewBox="0 0 24 24"><path d="M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z"/></svg>',
  logs: '<svg viewBox="0 0 24 24"><path d="M4 5h16v2H4zm0 4h16v2H4zm0 4h10v2H4zm0 4h13v2H4z"/></svg>',
  shield: '<svg viewBox="0 0 24 24"><path d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5zm-1.4 14.2-3.5-3.5 1.4-1.4 2.1 2.1 4.6-4.6 1.4 1.4z"/></svg>',
  pulse: '<svg viewBox="0 0 24 24"><path d="M3 13h4l2-5 4 10 2-5h6v-2h-7l-1 2.5L9 3.5 6.3 11H3z"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v12H3z"/></svg>',
  thermo: '<svg viewBox="0 0 24 24"><path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0zM12 20a2 2 0 0 1-1-3.7V9h2v7.3A2 2 0 0 1 12 20z"/></svg>',
  phone: '<svg viewBox="0 0 24 24"><path d="M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm0 3v13h8V5zm3 14.2v1h2v-1z"/></svg>',
  desktop: '<svg viewBox="0 0 24 24"><path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-7v2h3v2H7v-2h3v-2H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v9h16V6z"/></svg>',
  pi: '<svg viewBox="0 0 24 24"><path d="M4 5h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-5l1 2h2v2H6v-2h2l1-2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm0 2v9h16V7zm2 1.5h3v3H6z"/></svg>',
usb: '<svg viewBox="0 0 24 24"><path d="M7 2h10v7h1a1 1 0 0 1 1 1v9a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-9a1 1 0 0 1 1-1h1zm2 2v5h6V4zm1 1h1v2h-1zm3 0h1v2h-1zM7 11v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-8z"/></svg>',
  box: '<svg viewBox="0 0 24 24"><path d="M3 7l9-4 9 4v10l-9 4-9-4z"/></svg>',
};

/* ---------------- modal ---------------- */
function openModal(title, { pre = null, body = null } = {}) {
  $("#modal-title").textContent = title;
  const p = $("#modal-pre"), b = $("#modal-body");
  p.classList.toggle("hidden", pre === null); p.textContent = pre || "";
  b.replaceChildren(...(body ? [].concat(body) : []));
  $("#modal").classList.remove("hidden");
  return p;
}
function closeModal() { $("#modal").classList.add("hidden"); clearInterval(state.timers.job); }
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
function confirmBox(title, text, okLabel, danger = true) {
  return new Promise((res) => {
    const ok = el("button", { class: "btn " + (danger ? "danger" : "primary"), onclick: () => { closeModal(); res(true); } }, okLabel);
    const no = el("button", { class: "btn", onclick: () => { closeModal(); res(false); } }, "Cancel");
    openModal(title, { body: [el("p", { class: "muted" }, text), el("div", { class: "btnrow" }, no, ok)] });
  });
}

/* ---------------- auth ---------------- */
function showAuth(configured = true) {
  stopPolling(); hideScreensaver(true);
  $("#shell").classList.add("hidden"); $("#auth").classList.remove("hidden");
  // The Pi display never takes a password: it signs in with its kiosk token when the
  // kiosk browser starts. No admin sign-in on the server's own screen.
  const pi = LS.get("kiosk", false);
  $("#login-form").classList.toggle("hidden", !configured || pi);
  $("#setup-form").classList.toggle("hidden", configured || pi);
  $("#auth-msg").textContent = pi ? "This Pi display isn't signed in. Restart the kiosk (sudo systemctl restart piboard-kiosk) to reconnect." : "";
  if (pi) return;
  setTimeout(() => (configured ? $("#login-pw") : $("#setup-code")).focus(), 50);
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/login", { method: "POST", body: { password: $("#login-pw").value }, auth: true }); $("#login-pw").value = ""; boot(); }
  catch (err) { $("#auth-msg").textContent = err.message; }
});
$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("#setup-pw").value !== $("#setup-pw2").value) { $("#auth-msg").textContent = "Passwords don't match."; return; }
  try { await api("/api/setup", { method: "POST", body: { code: $("#setup-code").value, password: $("#setup-pw").value }, auth: true }); boot(); }
  catch (err) { $("#auth-msg").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); LS.set("kiosk", false); showAuth(true); });
$("#admin-login").addEventListener("click", () => showAuth(true));
$("#pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/password", { method: "POST", body: { current: $("#pw-cur").value, new: $("#pw-new").value } });
    $("#pw-cur").value = $("#pw-new").value = ""; toast("Password changed"); }
  catch (err) { toast(err.message, true); }
});

/* ---------------- navigation ---------------- */
function show(view) {
  state.view = view;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + view));
  window.scrollTo(0, 0);
  refresh();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => (b.dataset.href ? (location.href = b.dataset.href) : show(b.dataset.view))));

function stopPolling() { clearInterval(state.timers.poll); }
function startPolling() { stopPolling(); state.timers.poll = setInterval(() => { if (!document.hidden) refresh(true); }, 3000); }
async function refresh(background = false) {
  try {
    if (state.view === "dash") await loadDash();
    else if (state.view === "containers") await loadContainers();
    else if (state.view === "apps") {
      // app status changes rarely: refresh every 4th tick, and not while a dialog is open
      state.appTick = ((state.appTick || 0) + 1) % 4;
      if (!background || (state.appTick === 0 && $("#modal").classList.contains("hidden"))) await loadApps();
    }
    else if (state.view === "settings") {
      state.devTick = ((state.devTick || 0) + 1) % 5;
      if (!background || state.devTick === 0) await loadDevices();
      if (state.role === "admin" && !onPi() && (!background || state.devTick % 2 === 0)) await loadDisplay();
    }
  } catch (e) { if (!background) toast(e.message, true); }
}

/* ---------------- dashboard ---------------- */
function setRing(id, pct, text, sub, warnAt = 75, badAt = 90) {
  const s = $(id);
  $(".bar", s).style.strokeDasharray = `${Math.max(0, Math.min(100, pct))} 100`;
  $(".val", s).textContent = text;
  $(".sub", s).textContent = sub;
  s.classList.toggle("warn", pct >= warnAt && pct < badAt);
  s.classList.toggle("bad", pct >= badAt);
}
async function loadDash() {
  const [s, cs] = await Promise.all([api("/api/stats"), api("/api/containers")]);
  state.stats = s;
  $("#hostname").textContent = s.hostname;
  document.title = `${s.hostname} · PiBoard`;
  setRing("#st-cpu", s.cpu.percent, Math.round(s.cpu.percent) + "%", `${s.cpu.cores.length} cores`);
  setRing("#st-mem", s.mem.percent, Math.round(s.mem.percent) + "%", `${fmtBytes(s.mem.used)} / ${fmtBytes(s.mem.total)}`);
  const t = s.cpu.temp;
  // Pi 5 throttles at 85 C; ring scaled 30..90 C
  setRing("#st-temp", t == null ? 0 : (t - 30) / 60 * 100, t == null ? "n/a" : Math.round(t) + "°", t == null ? "" : (t >= 80 ? "hot!" : t >= 70 ? "warm" : "normal"), 66, 83);
  setRing("#st-disk", s.disk.percent, Math.round(s.disk.percent) + "%", `${fmtBytes(s.disk.free)} free`);
  $("#uptime").textContent = fmtUptime(s.uptime);
  $("#load").textContent = s.cpu.load.map((x) => x.toFixed(2)).join("  ");
  loadStorage().catch(() => {});
  const running = cs.filter((c) => c.state === "running");
  $("#ccount").textContent = `${running.length} / ${cs.length}`;
  $("#dash-running").replaceChildren(...(running.length ? running.map((c) => el("span", { class: "chip" }, el("i", { class: "dot running" }), c.name))
    : [el("span", { class: "muted small" }, "Nothing running")]));
}

/* ---------------- containers ---------------- */
// The Pi's own display (kiosk) is the server: it never gets options that can hurt it
// (sign out, stop, uninstall, update, password). The backend enforces this for the kiosk role too.
const onPi = () => state.role === "kiosk" || LS.get("kiosk", false);
const canControl = () => state.role === "admin" || (state.role === "kiosk" && state.settings && state.settings.kiosk_can_control);
async function containerAction(c, action, btn) {
  if (action === "stop" && !(await confirmBox(`Stop ${c.name}?`, "The container will be stopped until you start it again.", "Stop"))) return;
  btn.disabled = true;
  try { await api(`/api/containers/${encodeURIComponent(c.id)}/${action}`, { method: "POST" }); toast(`${c.name}: ${action} ok`); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; loadContainers(); }
}
async function showLogs(c) {
  const pre = openModal(`${c.name} · last 100 lines`, { pre: "Loading…" });
  const load = async () => {
    try { const r = await api(`/api/containers/${encodeURIComponent(c.id)}/logs`); pre.textContent = r.lines.join("\n") || "(no output)"; pre.scrollTop = pre.scrollHeight; }
    catch (e) { pre.textContent = e.message; }
  };
  await load();
  const body = el("div", { class: "btnrow" }, el("button", { class: "btn sm", onclick: load }, "Refresh"));
  $("#modal-body").replaceChildren(body);
}
async function loadContainers() {
  const cs = await api("/api/containers");
  const list = $("#container-list");
  if (!cs.length) { list.replaceChildren(el("div", { class: "empty" }, "No containers yet. Install something from Apps.")); return; }
  list.replaceChildren(...cs.map((c) => {
    const acts = el("div", { class: "acts" });
    const ib = (name, title, cls, fn) => { const b = el("button", { class: "iconbtn " + cls, title, "aria-label": title, html: ICONS[name] }); b.addEventListener("click", () => fn(b)); return b; };
    if (canControl()) {
      if (c.state === "running") {
        acts.append(ib("restart", "Restart", "", (b) => containerAction(c, "restart", b)));
        if (!c.self && !onPi()) acts.append(ib("stop", "Stop", "stop", (b) => containerAction(c, "stop", b)));
      } else acts.append(ib("play", "Start", "go", (b) => containerAction(c, "start", b)));
    }
    acts.append(ib("logs", "Logs", "", () => showLogs(c)));
    return el("div", { class: "crow" },
      el("div", { class: "meta" },
        el("div", { class: "name" }, el("i", { class: "dot " + c.state }), el("span", {}, c.name), c.self ? el("b", { class: "tag" }, "PIBOARD") : null),
        el("div", { class: "sub" }, `${c.status} · ${c.image}`)),
      acts);
  }));
}

/* ---------------- apps ---------------- */
function watchJob(id, title) {
  const pre = openModal(title, { pre: "Starting…" });
  clearInterval(state.timers.job);
  const tick = async () => {
    try {
      const j = await api(`/api/jobs/${id}`);
      pre.textContent = j.log.join("\n") + (j.done ? `\n\n${j.ok ? "✔ Done" : "✘ Failed"}` : "\n…");
      pre.scrollTop = pre.scrollHeight;
      if (j.done) { clearInterval(state.timers.job); toast(`${j.title}: ${j.ok ? "done" : "failed"}`, !j.ok); loadApps(); }
    } catch (e) { clearInterval(state.timers.job); pre.textContent += "\n" + e.message; }
  };
  tick(); state.timers.job = setInterval(tick, 1200);
}
async function appAction(a, action) {
  try {
    if (action === "uninstall") {
      const keep = await new Promise((res) => {
        openModal(`Uninstall ${a.title}?`, { body: [
          el("p", { class: "muted" }, `Containers will be removed. Data in ${a.path} can be kept for a later reinstall.`),
          el("div", { class: "btnrow" },
            el("button", { class: "btn", onclick: () => { closeModal(); res(null); } }, "Cancel"),
            el("button", { class: "btn", onclick: () => res(true) }, "Keep data"),
            el("button", { class: "btn danger", onclick: () => res(false) }, "Delete data")),
        ] });
      });
      if (keep === null) return;
      const r = await api(`/api/apps/${a.id}/uninstall?remove_data=${!keep}`, { method: "POST" });
      return watchJob(r.job, `Uninstall ${a.title}`);
    }
    const r = await api(`/api/apps/${a.id}/${action}`, { method: "POST" });
    watchJob(r.job, `${action[0].toUpperCase() + action.slice(1)} ${a.title}`);
  } catch (e) { toast(e.message, true); }
}
async function loadApps() {
  const apps = await api("/api/apps");
  const admin = state.role === "admin" && !onPi();
  $("#app-list").replaceChildren(...apps.map((a) => {
    const icon = el("div", { class: "app-icon", html: ICONS[a.icon] || ICONS.box }); icon.style.background = a.color || "#334";
    const btns = el("div", { class: "btnrow" });
    if (a.installed) {
      // not on the Pi display: a kiosk has no tabs or back button, so a new tab would trap it
      for (const p of onPi() ? [] : a.ports || []) {
        const url = `${location.protocol}//${location.hostname}:${p.port}${p.path || "/"}`;
        btns.append(el("a", { class: "btn sm primary", href: url, target: "_blank", rel: "noopener" }, `Open ${p.label || p.port}`));
      }
      if (admin) {
        if (a.state === "running") btns.append(el("button", { class: "btn sm", onclick: () => appAction(a, "stop") }, "Stop"));
        else btns.append(el("button", { class: "btn sm", onclick: () => appAction(a, "start") }, "Start"));
        btns.append(el("button", { class: "btn sm", onclick: () => appAction(a, "update") }, "Update"));
        btns.append(el("button", { class: "btn sm danger", onclick: () => appAction(a, "uninstall") }, "Uninstall"));
      }
    } else if (admin) {
      btns.append(el("button", { class: "btn sm primary", onclick: () => appAction(a, "install") }, "Install"));
    }
    return el("div", { class: "app" },
      el("div", { class: "app-head" }, icon,
        el("div", {}, el("div", { class: "app-title" }, a.title, a.installed ? el("i", { class: "dot " + a.state, title: a.state }) : null),
          el("div", { class: "app-desc" }, a.description))),
      btns,
      el("details", {}, el("summary", {}, "Details"),
        el("p", {}, a.notes || ""), el("p", {}, `Ports: ${(a.ports || []).map((p) => p.port).join(", ")} · Folder: ${a.path}`)));
  }));
}

/* ---------------- external storage (USB drives mounted by kiosk/hostd.py) ---------------- */
async function loadStorage() {
  const st = await api("/api/storage");
  const admin = state.role === "admin" && !onPi();
  const list = $("#storage-list");
  if (!st.drives.length) {
    list.replaceChildren(el("span", { class: "muted small" }, st.helper
      ? "No USB drive plugged in. Plug one into the Pi and it appears here within a few seconds."
      : "The storage helper isn't running yet. Restart the kiosk (sudo systemctl restart piboard-kiosk) or reboot the Pi."));
    return;
  }
  list.replaceChildren(...st.drives.map((d) => {
    const used = d.total ? d.total - d.free : 0;
    const pct = d.total ? used / d.total : 0;
    const bar = el("span", { class: "drive-bar" }, el("i"));
    bar.firstChild.style.transform = `scaleX(${pct})`;
    const row = el("div", { class: "drive" },
      el("span", { class: "dev-icon", html: ICONS.usb }),
      el("div", { class: "drive-name" }, d.label),
      d.mounted ? bar : null,
      el("div", { class: "drive-sub" }, d.mounted
        ? (d.total ? `${fmtBytes(d.free)} free of ${fmtBytes(d.total)} · ${d.fstype}` : d.path)
        : `${fmtBytes(d.size || 0)} · ${d.fstype} · not mounted`));
    if (admin && d.mounted) row.append(el("button", { class: "btn sm", onclick: async (e) => {
      if (!(await confirmBox(`Eject ${d.label}?`, "Apps using the drive lose access to it. Unplug it once it disappears from this list.", "Eject", false))) return;
      const b = e.target; b.disabled = true; b.textContent = "Ejecting…";
      try { await api(`/api/storage/${encodeURIComponent(d.name)}/eject`, { method: "POST" }); toast(`${d.label} ejected. You can unplug it.`); }
      catch (err) { toast(err.message, true); }
      loadStorage();
    } }, "Eject"));
    return row;
  }));
}

/* ---------------- Pi display: power, brightness, schedule ---------------- */
async function loadDisplay() {
  const d = await api("/api/display");
  state.display = d;
  $("#disp-state").textContent = !d.helper ? "Helper not running" : d.on ? "On" : "Off";
  $("#disp-state").classList.toggle("off", !d.on);
  $("#disp-toggle").textContent = d.on ? "Turn off" : "Turn on";
  $("#disp-toggle").disabled = !d.helper;
  if (document.activeElement !== $("#disp-bright")) $("#disp-bright").value = d.brightness;
  $("#disp-bright-val").textContent = `${d.brightness}%`;
  if (document.activeElement !== $("#disp-off-at")) $("#disp-off-at").value = d.off_at;
  if (document.activeElement !== $("#disp-on-at")) $("#disp-on-at").value = d.on_at;
}
async function saveDisplay(body, msg) {
  try { state.settings = await api("/api/settings", { method: "PUT", body }); toast(msg); }
  catch (e) { toast(e.message, true); }
  loadDisplay().catch(() => {});
}
$("#disp-toggle").addEventListener("click", async () => {
  const on = state.display && state.display.on;
  try { await api(`/api/display/${on ? "off" : "on"}`, { method: "POST" }); toast(on ? "Display turned off" : "Display turned on"); }
  catch (e) { toast(e.message, true); }
  setTimeout(() => loadDisplay().catch(() => {}), 1500);
});
$("#disp-bright").addEventListener("input", (e) => ($("#disp-bright-val").textContent = `${e.target.value}%`));
$("#disp-bright").addEventListener("change", (e) => saveDisplay({ display_brightness: +e.target.value }, `Brightness set to ${e.target.value}%`));
$("#disp-off-at").addEventListener("change", (e) => saveDisplay({ display_off_at: e.target.value },
  e.target.value ? `The display turns off every night at ${e.target.value}` : "Nightly turn-off is off"));
$("#disp-on-at").addEventListener("change", (e) => saveDisplay({ display_on_at: e.target.value },
  e.target.value ? `The display turns on every morning at ${e.target.value}` : "The display stays off until tapped"));

// On the Pi itself: apply the dimmer, and while the screen is off catch the tap that
// wakes it (hostd turns the HDMI back on at the touch) so it doesn't click anything.
const pi = { t: null, lastOn: true };
async function piDisplayTick() {
  try {
    const d = await api("/api/display");
    $("#dimmer").style.opacity = String(Math.min(.9, (100 - d.brightness) / 100));
    if (!d.on && pi.lastOn) $("#wake-catch").classList.remove("hidden");
    if (d.on && !pi.lastOn) setTimeout(() => $("#wake-catch").classList.add("hidden"), 400);
    pi.lastOn = d.on;
  } catch { /* offline: keep the last state */ }
}
function startPiDisplay() { clearInterval(pi.t); if (!onPi()) return; piDisplayTick(); pi.t = setInterval(piDisplayTick, 4000); }
["pointerdown", "touchstart", "click"].forEach((ev) => $("#wake-catch").addEventListener(ev, (e) => {
  e.preventDefault(); e.stopPropagation();
  setTimeout(() => $("#wake-catch").classList.add("hidden"), 400);
}, { passive: false }));
$("#display-off").addEventListener("click", async () => {
  $("#wake-catch").classList.remove("hidden"); pi.lastOn = false;
  try { await api("/api/display/off", { method: "POST" }); }
  catch (e) { $("#wake-catch").classList.add("hidden"); pi.lastOn = true; toast(e.message, true); }
});

/* ---------------- connected devices ---------------- */
const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t);
  return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
async function loadDevices() {
  const ds = await api("/api/sessions");
  const admin = state.role === "admin" && !onPi();
  $("#device-list").replaceChildren(...ds.map((d) => {
    const pi = d.role === "kiosk";
    const kind = pi ? "pi" : /iPhone|Android|iPad/.test(d.device) ? "phone" : "desktop";
    const when = d.current ? "this device" : d.seen ? `active ${ago(d.seen)}` : "not active since the last update";
    // Tailscale name first ("my-laptop"), browser and OS underneath
    const title = pi ? "Pi display" : d.name || d.device;
    const sub = pi ? "Server display · always signed in" : [d.name ? d.device : null, d.ip, when].filter(Boolean).join(" · ").replace(/^./, (c) => c.toUpperCase());
    const row = el("div", { class: "dev" + (pi ? " pi" : "") },
      el("span", { class: "dev-icon", html: ICONS[kind] }),
      el("div", { class: "dev-meta" },
        el("div", { class: "dev-name" }, el("span", {}, title), el("b", { class: "tag" + (d.role === "admin" ? " admin" : "") }, d.role === "admin" ? "ADMIN" : "READ-ONLY")),
        el("div", { class: "dev-sub" }, sub)));
    if (admin && !pi && !d.current) {
      row.append(el("button", { class: "btn sm danger", onclick: async (e) => {
        if (!(await confirmBox(`Sign out ${title}?`, "That browser will need the admin password to sign in again.", "Sign out"))) return;
        try { await api(`/api/sessions/${d.id}/revoke`, { method: "POST" }); toast("Device signed out"); }
        catch (err) { toast(err.message, true); }
        loadDevices();
      } }, "Sign out"));
    }
    return row;
  }));
}

/* ---------------- settings ---------------- */
function fillSettings() {
  const s = state.settings;
  $("#set-idle").value = s.screensaver_idle_minutes;
  renderSkyTimes();
  $("#set-24h").checked = s.screensaver_24h;
  $("#set-kiosk").checked = s.kiosk_can_control;
  $("#set-local").checked = screensaverEnabledHere();
  // the Pi display may change its clock format and idle time (saved on change); the rest is admin-only
  $$("#view-settings input:not(#set-local)").forEach((i) => (i.disabled = state.role !== "admin" && !["set-24h", "set-idle"].includes(i.id)));
  $("#role-line").textContent = state.role === "admin" ? "Signed in as admin." : "Kiosk session (read-only).";
}
$("#set-local").addEventListener("change", (e) => { LS.set("screensaver", e.target.checked); resetIdle(); });
// not admin (the Pi display): there is no Save button, so these two save as soon as they change
async function saveKioskPref(body, msg) {
  if (state.role === "admin") return;
  try { state.settings = await api("/api/settings", { method: "PUT", body }); fillSettings(); resetIdle(); toast(msg); }
  catch (e) { toast(e.message, true); fillSettings(); }
}
$("#set-24h").addEventListener("change", (e) => saveKioskPref({ screensaver_24h: e.target.checked }, e.target.checked ? "24-hour clock on" : "12-hour clock on"));
$("#set-idle").addEventListener("change", (e) => saveKioskPref({ screensaver_idle_minutes: Math.max(0, +e.target.value || 0) },
  +e.target.value ? `Screensaver after ${+e.target.value} min idle` : "Screensaver turned off"));
$("#set-save").addEventListener("click", async () => {
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: {
      screensaver_idle_minutes: +$("#set-idle").value,
      screensaver_24h: $("#set-24h").checked, kiosk_can_control: $("#set-kiosk").checked } });
    fillSettings(); resetIdle(); toast("Saved");
  } catch (e) { toast(e.message, true); }
});
$("#set-kiosk").addEventListener("change", () => $("#set-save").click());
$("#ss-preview").addEventListener("click", (e) => { e.stopPropagation(); showScreensaver(true); });

/* ==================== screensaver ==================== */
const ss = { on: false, images: [], idx: 0, layer: 0, idleT: null, clockT: null, slideT: null, tempT: null, wxT: null, previewT: null, force: null };
function screensaverEnabledHere() { return LS.get("screensaver", LS.get("kiosk", false)); }
function resetIdle() {
  clearTimeout(ss.idleT);
  const mins = state.settings ? state.settings.screensaver_idle_minutes : 0;
  if (!state.role || !mins || !screensaverEnabledHere()) return;
  ss.idleT = setTimeout(showScreensaver, mins * 60000);
}
["pointerdown", "pointermove", "keydown", "wheel", "touchstart"].forEach((ev) =>
  document.addEventListener(ev, () => { if (!ss.on) resetIdle(); }, { passive: true, capture: true }));

/* ---- generated sky: scene by the real sun at your location, crossfaded via CSS ----
   Sunrise/sunset are computed here (standard solar equations, as in SunCalc), so it
   works offline and moves through the seasons. Location: WEATHER_LAT/LON (set in .env). */
function sunTimes(date, lat, lon) {
  const rad = Math.PI / 180, dayMs = 864e5, J1970 = 2440588, J2000 = 2451545, J0 = 0.0009, e = rad * 23.4397;
  const lw = rad * -lon, phi = rad * lat;
  const d = date.valueOf() / dayMs - 0.5 + J1970 - J2000;
  const n = Math.round(d - J0 - lw / (2 * Math.PI));
  const ds = J0 + lw / (2 * Math.PI) + n;
  const M = rad * (357.5291 + 0.98560028 * ds);
  const L = M + rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M)) + rad * 102.9372 + Math.PI;
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const transit = (x) => J2000 + x + 0.0053 * Math.sin(M) - 0.0069 * Math.sin(2 * L);
  const noon = transit(ds);
  const w = Math.acos((Math.sin(rad * -0.833) - Math.sin(phi) * Math.sin(dec)) / (Math.cos(phi) * Math.cos(dec)));
  const set = transit(J0 + (w + lw) / (2 * Math.PI) + n), rise = noon - (set - noon);
  const toDate = (j) => new Date((j + 0.5 - J1970) * dayMs);
  return isNaN(w) ? null : { rise: toDate(rise), set: toDate(set) };   // null: polar day/night
}
// Scene windows around today's sun: sunrise from 30 min before sunrise for 3 h, sunset
// from 90 min before sunset to 40 min after, night after that, day in between.
const sunCache = { key: "", t: null };
function sceneWindows(now = new Date()) {
  const key = now.toDateString();
  if (sunCache.key !== key) {
    const loc = (state.settings && state.settings.location) || { lat: 24.21, lon: 55.74 };
    sunCache.key = key; sunCache.t = sunTimes(new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12), loc.lat, loc.lon);
  }
  const t = sunCache.t, at = (h, m = 0) => new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m);
  const rise = t ? t.rise : at(6), set = t ? t.set : at(18), min = 60000;
  return { dawn: new Date(rise - 30 * min), dayFrom: new Date(+rise + 150 * min), dusk: new Date(set - 90 * min), night: new Date(+set + 40 * min), rise, set };
}
function sceneFor(now = new Date()) {
  const w = sceneWindows(now);
  if (now >= w.dawn && now < w.dayFrom) return "morning";
  if (now >= w.dayFrom && now < w.dusk) return "afternoon";
  if (now >= w.dusk && now < w.night) return "evening";
  return "night";
}
const hm = (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
function sceneRange(sc) {
  const w = sceneWindows();
  return { morning: [w.dawn, w.dayFrom], afternoon: [w.dayFrom, w.dusk], evening: [w.dusk, w.night], night: [w.night, w.dawn] }[sc].map(hm).join("–");
}
function renderSkyTimes() {
  const w = sceneWindows();
  $("#sky-times").textContent = `The background is a generated sky that follows the real sun for your location. Today: sunrise ${hm(w.rise)}, sunset ${hm(w.set)}. The sky shows sunrise ${sceneRange("morning")}, day ${sceneRange("afternoon")}, sunset ${sceneRange("evening")} and night ${sceneRange("night")}. The weather is under the clock.`;
}
// One landscape (a desert massif and dunes) lit differently per scene.
// [far → near layer colours], haze = colour that pools in the valleys.
const SKY_LAND = {
  morning:   { layers: ["#b39bb7", "#86709a", "#56476e", "#2b2239"], haze: "#ffd9c0", hazeA: [.55, .35, .18] },
  afternoon: { layers: ["#9fb4cc", "#d8bb92", "#c39463", "#9a6a3b"], haze: "#eaf3fa", hazeA: [.6, .3, .12] },
  evening:   { layers: ["#a24f6f", "#6e2e5c", "#3e183f", "#1b0b20"], haze: "#ff9d68", hazeA: [.5, .3, .12] },
  night:     { layers: ["#1e2d55", "#141f40", "#0b132a", "#04070f"], haze: "#2e4580", hazeA: [.35, .2, .08] },
};
let skyBuilt = false;
function buildSky() {
  if (skyBuilt) return; skyBuilt = true;
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  // Seeded PRNG: the same landscape and stars every time.
  let seed = 20261106;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296);
  // smooth 1-D value noise
  const noise = (n) => { const k = Array.from({ length: n + 2 }, rnd);
    return (t) => { const i = Math.floor(t), f = t - i, u = f * f * (3 - 2 * f); return k[i % (n + 1)] * (1 - u) + k[(i + 1) % (n + 1)] * u; }; };
  const ridge = (base, amp, scale, oct, peak) => {
    const ns = Array.from({ length: oct }, () => noise(64));
    let d = "M0 200";
    for (let x = 0; x <= 800; x += 4) {
      let y = 0, a = 1, f = 1, tot = 0;
      for (let o = 0; o < oct; o++) { y += ns[o](x / scale * f) * a; tot += a; a *= .45; f *= 2.2; }
      y = base - amp * (y / tot);
      if (peak) y -= peak.h * Math.exp(-(((x - peak.x) / peak.w) ** 2));   // the massif
      d += ` L${x} ${y.toFixed(1)}`;
    }
    return d + " L800 200 Z";
  };
  const ridges = [
    ridge(96, 40, 110, 4, { x: 170, w: 120, h: 62 }),   // far: desert massif
    ridge(118, 30, 120, 3),
    ridge(146, 22, 170, 2),                              // dunes: smooth
    ridge(176, 16, 130, 2),
  ];
  for (const [sc, cfg] of Object.entries(SKY_LAND)) {
    const svg = $(`.sky-${sc} .land`), defs = mk("defs");
    const g = mk("linearGradient", { id: `haze-${sc}`, x1: 0, y1: 0, x2: 0, y2: 1 });
    g.append(mk("stop", { offset: 0, "stop-color": cfg.haze, "stop-opacity": 0 }), mk("stop", { offset: 1, "stop-color": cfg.haze, "stop-opacity": 1 }));
    defs.append(g); svg.append(defs);
    ridges.forEach((d, i) => {
      svg.append(mk("path", { d, fill: cfg.layers[i] }));
      if (i < 3) svg.append(mk("rect", { x: 0, y: 40 + i * 30, width: 800, height: 90, fill: `url(#haze-${sc})`, opacity: cfg.hazeA[i] }));
    });
  }
  // stars: mostly tiny, a few bright with a halo, slightly varied colour, denser along the Milky Way
  const tints = ["#ffffff", "#ffffff", "#dce7ff", "#fff1d6"];
  const field = (n, rMin, rMax, band = 0, halo = false) => {
    const svg = mk("svg", { viewBox: "0 0 800 480", preserveAspectRatio: "xMidYMid slice" });
    for (let i = 0; i < n; i++) {
      let x = rnd() * 800, y;
      if (rnd() < band) { const off = (rnd() + rnd() + rnd() - 1.5) * 60; y = 300 - x * .4 + off; }  // along the band
      else y = rnd() * rnd() * 400;
      if (y < 0 || y > 400) continue;
      const r = rMin + rnd() ** 2 * (rMax - rMin), fill = tints[Math.floor(rnd() * tints.length)];
      if (halo) svg.append(mk("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: (r * 3.5).toFixed(2), fill, opacity: .12 }));
      svg.append(mk("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: r.toFixed(2), fill, opacity: (.5 + rnd() * .5).toFixed(2) }));
    }
    return svg;
  };
  $(".sky-night .tw1").append(field(150, .35, .8, .55));
  $(".sky-night .tw2").append(field(90, .45, 1.0, .4));
  $(".sky-night .tw3").append(field(50, .6, 1.3, .2));
  $(".sky-night .tw4").append(field(14, 1.1, 1.8, 0, true));
  $(".sky-evening .stars").append(field(12, .5, 1, 0));
}
const SCENES = ["morning", "afternoon", "evening", "night"];
const SCENE_LABEL = { morning: "Sunrise", afternoon: "Day", evening: "Sunset", night: "Night" };
// wallpaper file names can be tagged for a time of day
const SCENE_TAGS = { morning: /sunrise|morning|dawn/i, afternoon: /(^|[^a-z])day|noon|afternoon/i, evening: /sunset|evening|dusk/i, night: /night/i };
function tickSky() {
  const sc = ss.force || sceneFor();
  $$("#screensaver .sky").forEach((el) => el.classList.toggle("show", el.dataset.scene === sc));
  if (sc !== ss.scene) { ss.scene = sc; pickWallpaper(sc); }   // wallpapers change with the time of day
}
function pickWallpaper(sc) {
  if (!ss.images.length) return;
  const tagged = ss.images.filter((f) => SCENE_TAGS[sc].test(f));
  const anyTag = ss.images.some((f) => Object.values(SCENE_TAGS).some((re) => re.test(f)));
  const pool = tagged.length ? tagged : anyTag ? ss.images.filter((f) => !Object.values(SCENE_TAGS).some((re) => re.test(f))) : ss.images;
  const list = pool.length ? pool : ss.images;
  // untagged: step through the images, one per time-of-day change
  ss.idx = (SCENES.indexOf(sc) + Math.floor(Date.now() / 864e5) * 4) % list.length;
  setLayer(`/api/screensaver/images/${encodeURIComponent(list[ss.idx])}`);
}

/* ---- weather (Open-Meteo via /api/weather; cached server-side and here) ---- */
const WX = (() => {
  const rays = Array.from({ length: 8 }, (_, i) => { const a = i * Math.PI / 4;
    return `<line class="i-ray" x1="${(16 + 9 * Math.sin(a)).toFixed(2)}" y1="${(16 - 9 * Math.cos(a)).toFixed(2)}" x2="${(16 + 12.5 * Math.sin(a)).toFixed(2)}" y2="${(16 - 12.5 * Math.cos(a)).toFixed(2)}"/>`; }).join("");
  const cloudD = "M9.5 25H23.5A5.5 5.5 0 0 0 24.4 14.07A7.6 7.6 0 0 0 10.2 12.6A6.2 6.2 0 0 0 9.5 25Z";
  return {
    sun: `<g class="i-rays">${rays}</g><circle class="i-sun-core" cx="16" cy="16" r="6.2"/>`,
    moon: '<path class="i-moon" d="M25 20.2A10.5 10.5 0 0 1 11.8 7 10.5 10.5 0 1 0 25 20.2Z"/>',
    cloud: (cls = "i-cloud", t = "") => `<path class="${cls}" d="${cloudD}" ${t ? `transform="${t}"` : ""}/>`,
  };
})();
function wxIcon(code, day) {
  const orb = day ? `<g transform="translate(-3.5 -4.5) scale(.78)">${WX.sun}</g>` : `<g transform="translate(-2.5 -4) scale(.78)">${WX.moon}</g>`;
  const up = "translate(0 -4)";
  const drops = (cls) => [11, 16, 21].map((x, i) => cls === "i-flake"
    ? `<g class="i-flake d${i + 1}"><path d="M${x} 27v3.4M${x - 1.5} 27.85l3 1.7M${x - 1.5} 29.55l3-1.7"/></g>`
    : `<line class="i-drop d${i + 1}" x1="${x}" y1="${cls === "drizzle" ? 25 : 24.5}" x2="${x - 1.2}" y2="${cls === "drizzle" ? 27 : 29}"/>`).join("");
  let body, label;
  if (code <= 1) { body = day ? WX.sun : WX.moon; label = code ? "Mostly clear" : "Clear"; }
  else if (code === 2) { body = orb + WX.cloud("i-cloud", "translate(2 2)"); label = "Partly cloudy"; }
  else if (code === 3) { body = WX.cloud("i-cloud-back", "translate(4 -4) scale(.8)") + WX.cloud("i-cloud", "translate(-1 1)"); label = "Cloudy"; }
  else if (code === 45 || code === 48) { body = '<path class="i-fog" d="M6 11h20M4 16h24M7 21h18M10 26h12"/>'; label = "Fog"; }
  else if (code >= 51 && code <= 57) { body = WX.cloud("i-cloud", up) + drops("drizzle"); label = "Drizzle"; }
  else if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) { body = WX.cloud("i-cloud", up) + drops("rain"); label = code >= 80 ? "Showers" : "Rain"; }
  else if ((code >= 71 && code <= 77) || code === 85 || code === 86) { body = WX.cloud("i-cloud", up) + drops("i-flake"); label = "Snow"; }
  else if (code >= 95) { body = WX.cloud("i-cloud-dark", up) + '<path class="i-bolt" d="M17.2 19 12.4 25.6h3.4L14 31l5.6-7.2h-3.4l1.9-4.8Z"/>'; label = "Thunderstorm"; }
  else { body = WX.cloud(); label = "Cloudy"; }
  return { svg: `<svg viewBox="0 0 32 32" aria-hidden="true">${body}</svg>`, label };
}
function renderWx(w) {
  const box = $("#ss-wx");
  if (!w || typeof w.temp !== "number") return box.classList.add("hidden");
  const ic = wxIcon(w.code, ss.force ? ss.force !== "night" : w.is_day);   // preview: match the sky
  $("#ss-wx-icon").innerHTML = ic.svg;
  $("#ss-wx-temp").textContent = `${Math.round(w.temp)}°C`;
  box.setAttribute("aria-label", `${ic.label}, ${Math.round(w.temp)}°C`);
  const ageMin = Math.round((Date.now() / 1000 - w.fetched) / 60);
  box.title = `Weather · updated ${ageMin} min ago`;
  box.classList.toggle("stale", ageMin > 180);    // older than 3 h: offline, dim it
  box.classList.remove("hidden");
}
async function tickWeather() {
  renderWx(LS.get("weather", null));             // last known reading first (works offline)
  try { const w = await api("/api/weather"); LS.set("weather", w); renderWx(w); }
  catch { /* offline: keep the cached reading */ }
}

function tickClock() {
  const d = new Date();
  const h24 = !state.settings || state.settings.screensaver_24h;
  let h = d.getHours(); const m = String(d.getMinutes()).padStart(2, "0");
  if (!h24) h = h % 12 || 12;
  $("#ss-hh").textContent = h24 ? String(h).padStart(2, "0") : h;
  $("#ss-mm").textContent = m;
  const wd = d.toLocaleDateString("en-GB", { weekday: "long" });
  const mo = d.toLocaleDateString("en-GB", { month: "long" });
  $("#ss-date").textContent = `${wd} · ${d.getDate()} ${mo}`;
  tickSky();   // cheap; flips the scene within a second of 05/12/17/20:00
}
async function tickTemp() {
  try { const s = await api("/api/stats"); const t = s.cpu.temp;
    $("#ss-temp").innerHTML = t == null ? "" : ICONS.thermo; if (t != null) $("#ss-temp").append(`${Math.round(t)}°C`); }
  catch { /* ignore */ }
}
function setLayer(url) {
  const layers = [$("#ss-a"), $("#ss-b")];
  const next = layers[ss.layer ^ 1], cur = layers[ss.layer];
  const img = new Image();
  img.onload = () => {
    next.style.backgroundImage = `url("${url}")`;
    next.classList.add("show"); cur.classList.remove("show");
    ss.layer ^= 1;
  };
  img.src = url;
}
async function showScreensaver(preview = false) {
  if (ss.on || !state.role) return;
  ss.on = true;
  const el_ = $("#screensaver");
  // Preview tours all four skies (tap to exit); otherwise the sky follows the clock.
  ss.force = null; $("#ss-badge").classList.add("hidden"); el_.classList.toggle("preview", preview);
  if (preview) {
    let i = 0;
    const step = () => {
      ss.force = SCENES[i++ % SCENES.length]; tickSky();
      $("#ss-badge").textContent = `Preview · ${SCENE_LABEL[ss.force]} ${sceneRange(ss.force)}`;
      renderWx(LS.get("weather", null));
    };
    buildSky(); step(); $("#ss-badge").classList.remove("hidden");
    ss.previewT = setInterval(step, 12000);   // 2 s crossfade (CSS .preview) + ~10 s to look
  }
  ss.scene = null;
  buildSky(); tickSky(); tickClock(); tickTemp(); tickWeather();
  el_.classList.remove("hidden");
  requestAnimationFrame(() => requestAnimationFrame(() => el_.classList.add("on")));
  // Tick just after each real second boundary, not at a random offset.
  const loop = () => { tickClock(); ss.clockT = setTimeout(loop, 1010 - (Date.now() % 1000)); };
  ss.clockT = setTimeout(loop, 1010 - (Date.now() % 1000));
  ss.tempT = setInterval(tickTemp, 15000);
  ss.wxT = setInterval(tickWeather, 15 * 60000);
  try { ss.images = await api("/api/screensaver/images"); } catch { ss.images = []; }
  $$(".ss-bg").forEach((l) => l.classList.remove("show"));
  if (ss.images.length) pickWallpaper(ss.scene);
}
function hideScreensaver(instant = false) {
  if (!ss.on) return;
  ss.on = false;
  [ss.clockT, ss.tempT, ss.slideT, ss.wxT, ss.previewT].forEach(clearInterval);
  ss.force = null;
  const el_ = $("#screensaver");
  el_.classList.remove("on");
  // keep catching pointer events until the fade ends so the wake-up tap
  // doesn't click whatever is underneath
  setTimeout(() => { if (!ss.on) el_.classList.add("hidden"); }, instant ? 0 : 650);
  resetIdle();
}
["pointerdown", "touchstart", "click"].forEach((ev) =>
  $("#screensaver").addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); hideScreensaver(); }, { passive: false }));
document.addEventListener("keydown", () => { if (ss.on) hideScreensaver(); });

/* ---------------- boot ---------------- */
async function kioskLogin() {
  const token = location.hash.slice(1);
  history.replaceState(null, "", "/");
  if (!token) return;
  try { await api("/api/kiosk-login", { method: "POST", body: { token }, auth: true }); LS.set("kiosk", true); }
  catch (e) { console.warn("kiosk login failed", e); }
}
async function boot() {
  if (location.pathname === "/kiosk") await kioskLogin();
  let st;
  try { st = await api("/api/status", { auth: true }); } catch (e) { $("#auth-msg").textContent = e.message; return showAuth(true); }
  if (!st.configured) return showAuth(false);
  if (!st.role) return showAuth(true);
  state.role = st.role;
  document.body.classList.toggle("admin", st.role === "admin");
  document.body.classList.toggle("kiosk", st.role === "kiosk");
  document.body.classList.toggle("onpi", onPi());
  startPiDisplay();
  state.settings = await api("/api/settings");
  fillSettings();
  $("#auth").classList.add("hidden"); $("#shell").classList.remove("hidden");
  show(state.view); startPolling(); resetIdle();
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.role) refresh(true); });
boot();
