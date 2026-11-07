"""PiBoard - lightweight self-hosted home-server dashboard.

Single FastAPI process. Talks to Docker through the docker CLI (mounted
socket), reads host stats from /proc and /sys, and serves a static SPA.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import asyncio
import struct
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --------------------------------------------------------------------------
# Configuration (all from environment; nothing secret is hard-coded)
# --------------------------------------------------------------------------
PIBOARD_HOME = Path(os.environ.get("PIBOARD_HOME", "/piboard"))  # host path, mounted 1:1
APPS_DIR = PIBOARD_HOME / "apps"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SCREENSAVER_DIR = Path(os.environ.get("SCREENSAVER_DIR", "/screensaver"))
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", "/app/templates"))
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))
HOST_HOSTNAME_FILE = Path(os.environ.get("HOST_HOSTNAME_FILE", "/host/hostname"))
SELF_CONTAINER = os.environ.get("SELF_CONTAINER", "piboard")
SELF_IMAGE = os.environ.get("SELF_IMAGE", "piboard:local")

APP_ENV = {
    "LOCAL_IP": os.environ.get("LOCAL_IP", "127.0.0.1"),
    "TAILSCALE_IP": os.environ.get("TAILSCALE_IP", ""),
    "PUID": os.environ.get("PUID", "1000"),
    "PGID": os.environ.get("PGID", "1000"),
    "TZ": os.environ.get("TZ", "UTC"),
}

SESSION_COOKIE = "piboard_session"
SESSION_TTL = 30 * 24 * 3600
AUTH_FILE = DATA_DIR / "auth.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SETUP_CODE_FILE = DATA_DIR / "setup.code"
KIOSK_TOKEN_FILE = DATA_DIR / "kiosk.token"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "screensaver_idle_minutes": 2,   # 0 disables
    "screensaver_slide_seconds": 30,  # crossfade interval when >1 image
    "screensaver_24h": True,
    "kiosk_can_control": False,       # may the kiosk session start/stop things?
    "display_brightness": 100,        # Pi display, 10-100 % (software dimming: the panel has no backlight control)
    "display_off_at": "00:30",        # daily auto-off, "" = never (kiosk/hostd.py applies it)
    "display_on_at": "",              # daily auto-on, "" = stay off until the screen is tapped
}
HOST_REQ_DIR = DATA_DIR / "host-requests"   # read by kiosk/hostd.py on the host
HOST_STATUS = DATA_DIR / "host-status.json"
MEDIA_DIR = Path("/media")                   # host /media, bind-mounted read-only (rslave)
DRIVE_RE = re.compile(r"^(sd[a-z]{1,2}\d{0,3}|nvme\d+n\d+(p\d+)?|mmcblk[1-9]p?\d{0,3})$")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")

_lock = threading.RLock()


# --------------------------------------------------------------------------
# Small file helpers
# --------------------------------------------------------------------------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _write_json(path: Path, data) -> None:
    _write_private(path, json.dumps(data, indent=2))


# --------------------------------------------------------------------------
# Password hashing (stdlib scrypt)
# --------------------------------------------------------------------------
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                        maxmem=128 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                              p=int(p), maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def is_configured() -> bool:
    return bool(_read_json(AUTH_FILE, {}).get("hash"))


# --------------------------------------------------------------------------
# Sessions (stored hashed on disk so restarts don't log the kiosk out)
# --------------------------------------------------------------------------
def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_sessions() -> dict:
    now = time.time()
    s = _read_json(SESSIONS_FILE, {})
    return {k: v for k, v in s.items() if v.get("exp", 0) > now}


SEEN_EVERY = 60   # write "last seen" to disk at most once a minute per session


def device_name(ua: str) -> str:
    """Short, human label from a User-Agent, e.g. 'Chrome on Android'."""
    ua = ua or ""
    os_ = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
           else "Windows" if "Windows" in ua else "ChromeOS" if "CrOS" in ua
           else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "")
    br = ("Edge" if "Edg/" in ua else "Samsung Internet" if "SamsungBrowser" in ua
          else "Firefox" if ("Firefox/" in ua or "FxiOS" in ua) else "Chrome" if ("Chrome/" in ua or "CriOS" in ua)
          else "Safari" if "Safari/" in ua else "")
    if br and os_:
        return f"{br} on {os_}"
    return br or os_ or "Unknown device"


# ---- device names from Tailscale ----
# Browsers don't reveal a device's name, but every tailnet device has one. A reverse
# (PTR) lookup of the client's 100.x address at MagicDNS (100.100.100.100) returns e.g.
# "my-laptop.tailnet-name.ts.net". Plain stdlib DNS over UDP, cached, short timeout.
_ts_names: dict[str, tuple[float, str | None]] = {}
TS_NAME_TTL = 600


def _is_tailnet_ip(ip: str) -> bool:
    try:
        a, b, *_ = (int(x) for x in ip.split("."))
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127          # 100.64.0.0/10 (CGNAT range Tailscale uses)


def _ptr_lookup(ip: str) -> str | None:
    import socket
    import struct
    qname = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    tid = secrets.randbelow(65536)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in qname.split(".")) + b"\0" + struct.pack(">HH", 12, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1.5)
        sock.sendto(q, ("100.100.100.100", 53))
        data = sock.recv(1024)
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != tid or struct.unpack(">H", data[6:8])[0] == 0:
        return None
    i = 12
    while data[i]:                      # skip the question name
        i += data[i] + 1
    i += 5                              # null + qtype + qclass
    if data[i] & 0xC0 == 0xC0:          # answer name (compressed pointer)
        i += 2
    else:
        while data[i]:
            i += data[i] + 1
        i += 1
    i += 10                             # type, class, ttl, rdlength
    label = data[i + 1:i + 1 + data[i]].decode("ascii", "replace")   # first label = host name
    return re.sub(r"[^A-Za-z0-9._-]", "", label)[:63] or None


def tailnet_name(ip: str | None) -> str | None:
    if not ip or not _is_tailnet_ip(ip):
        return None
    now = time.time()
    hit = _ts_names.get(ip)
    if hit and now - hit[0] < TS_NAME_TTL:
        return hit[1]
    try:
        name = _ptr_lookup(ip)
    except (OSError, IndexError, ValueError):
        name = hit[1] if hit else None      # keep the last good answer if DNS is down
    _ts_names[ip] = (now, name)
    return name


def create_session(role: str, ua: str = "", ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        s = _load_sessions()
        if role == "kiosk":
            # there is one Pi display: a new kiosk sign-in replaces the old one(s)
            s = {k: v for k, v in s.items() if v.get("role") != "kiosk"}
        s[_tok_hash(token)] = {"role": role, "exp": now + SESSION_TTL, "created": now, "seen": now, "ip": ip,
                               "device": "Pi display" if role == "kiosk" else device_name(ua)[:60]}
        _write_json(SESSIONS_FILE, s)
    return token


def drop_session(token: str | None) -> None:
    if not token:
        return
    with _lock:
        s = _load_sessions()
        s.pop(_tok_hash(token), None)
        _write_json(SESSIONS_FILE, s)


def drop_all_sessions() -> None:
    """Sign out every browser except the Pi display (it uses its token, not the password)."""
    with _lock:
        _write_json(SESSIONS_FILE, {k: v for k, v in _load_sessions().items() if v.get("role") == "kiosk"})


def session_role(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    h = _tok_hash(token)
    with _lock:
        s = _load_sessions()
        sess = s.get(h)
        ip = request.client.host if request.client else ""
        if sess and (time.time() - sess.get("seen", 0) > SEEN_EVERY or sess.get("ip") != ip):
            sess["seen"] = time.time()
            sess["ip"] = ip
            if not sess.get("device") or sess["device"] == "Unknown device":
                sess["device"] = device_name(request.headers.get("user-agent", ""))[:60]
            _write_json(SESSIONS_FILE, s)
    return sess["role"] if sess else None


def session_id(token_hash: str) -> str:
    # public handle for a session: a prefix of the token's SHA-256, never the token
    return token_hash[:16]


def set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
                    samesite="strict", secure=False, path="/")


# --------------------------------------------------------------------------
# Login rate limiting
#   Behind Docker's userland proxy every client may appear with the same
#   source IP, so we apply both a per-IP and a global limit.
# --------------------------------------------------------------------------
RL_WINDOW = 15 * 60
RL_PER_IP = 5
RL_GLOBAL = 20
_fails: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_fails_global: collections.deque = collections.deque()


def _prune(dq: collections.deque, now: float) -> None:
    while dq and dq[0] < now - RL_WINDOW:
        dq.popleft()


def rate_limit_check(ip: str) -> None:
    now = time.time()
    with _lock:
        _prune(_fails[ip], now)
        _prune(_fails_global, now)
        blocked = None
        if len(_fails[ip]) >= RL_PER_IP:
            blocked = _fails[ip][0] + RL_WINDOW - now
        elif len(_fails_global) >= RL_GLOBAL:
            blocked = _fails_global[0] + RL_WINDOW - now
    if blocked is not None:
        raise HTTPException(429, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(int(blocked) + 1)})


def rate_limit_fail(ip: str) -> None:
    now = time.time()
    with _lock:
        _fails[ip].append(now)
        _fails_global.append(now)


def rate_limit_reset(ip: str) -> None:
    with _lock:
        _fails.pop(ip, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in _read_json(SETTINGS_FILE, {}).items() if k in DEFAULT_SETTINGS})
    return s


# --------------------------------------------------------------------------
# First-run bootstrap
# --------------------------------------------------------------------------
def bootstrap() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    if not KIOSK_TOKEN_FILE.exists():
        _write_private(KIOSK_TOKEN_FILE, secrets.token_urlsafe(32))
    if not is_configured():
        if not SETUP_CODE_FILE.exists():
            _write_private(SETUP_CODE_FILE, secrets.token_hex(4).upper())
        code = SETUP_CODE_FILE.read_text().strip()
        print("=" * 60, flush=True)
        print(f" PiBoard first-run setup code: {code}", flush=True)
        print(f" (also in {PIBOARD_HOME}/data/setup.code)", flush=True)
        print("=" * 60, flush=True)


# --------------------------------------------------------------------------
# System stats
# --------------------------------------------------------------------------
class CpuSampler(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.percent = 0.0
        self.per_core: list[float] = []

    @staticmethod
    def _read():
        rows = []
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu"):
                    parts = line.split()
                    vals = list(map(int, parts[1:9]))
                    idle = vals[3] + vals[4]
                    rows.append((sum(vals), idle))
        return rows

    def run(self):
        prev = self._read()
        while True:
            time.sleep(2)
            cur = self._read()
            usage = []
            for (t1, i1), (t2, i2) in zip(prev, cur):
                dt = t2 - t1
                usage.append(round(100.0 * (1 - (i2 - i1) / dt), 1) if dt > 0 else 0.0)
            if usage:
                self.percent, self.per_core = usage[0], usage[1:]
            prev = cur


cpu_sampler = CpuSampler()


def cpu_temp() -> float | None:
    base = Path("/sys/class/thermal")
    for zone in sorted(base.glob("thermal_zone*")):
        try:
            if (zone / "type").read_text().strip() in ("cpu-thermal", "cpu_thermal", "soc_thermal") \
                    or zone.name == "thermal_zone0":
                return round(int((zone / "temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            continue
    return None


def mem_info() -> dict:
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0]) * 1024
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
    return {"total": total, "used": total - avail,
            "percent": round(100 * (total - avail) / total, 1) if total else 0,
            "swap_total": m.get("SwapTotal", 0),
            "swap_used": m.get("SwapTotal", 0) - m.get("SwapFree", 0)}


def disk_info() -> dict:
    # DATA_DIR lives on the host's root filesystem, so statvfs on it reports
    # the host disk without mounting all of / into the container.
    st = os.statvfs(DATA_DIR)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - st.f_bfree * st.f_frsize
    return {"total": total, "used": used, "free": free,
            "percent": round(100 * used / total, 1) if total else 0}


def host_name() -> str:
    try:
        return HOST_HOSTNAME_FILE.read_text().strip()
    except OSError:
        return os.uname().nodename


def stats() -> dict:
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    with open("/proc/loadavg") as f:
        load = [float(x) for x in f.read().split()[:3]]
    return {
        "hostname": host_name(),
        "cpu": {"percent": cpu_sampler.percent, "cores": cpu_sampler.per_core,
                "load": load, "temp": cpu_temp()},
        "mem": mem_info(),
        "disk": disk_info(),
        "uptime": int(uptime),
        "time": time.time(),
    }


# --------------------------------------------------------------------------
# Docker helpers (CLI over the mounted socket; never via a shell)
# --------------------------------------------------------------------------
def docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def list_containers() -> list[dict]:
    r = docker("ps", "-a", "--no-trunc", "--format", "{{json .}}")
    if r.returncode != 0:
        raise HTTPException(502, f"docker error: {r.stderr.strip()[:300]}")
    out = []
    for line in r.stdout.splitlines():
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        labels = dict(kv.split("=", 1) for kv in c.get("Labels", "").split(",") if "=" in kv)
        out.append({
            "id": c.get("ID", "")[:12],
            "name": c.get("Names", ""),
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": c.get("Status", ""),
            "ports": c.get("Ports", ""),
            "project": labels.get("com.docker.compose.project", ""),
            "self": c.get("Names", "") == SELF_CONTAINER,
        })
    out.sort(key=lambda c: (c["state"] != "running", c["name"]))
    return out


# --------------------------------------------------------------------------
# App store (compose templates) + background jobs
# --------------------------------------------------------------------------
def load_templates() -> dict[str, dict]:
    t = {}
    for d in sorted(TEMPLATES_DIR.iterdir()):
        meta = d / "app.json"
        if d.is_dir() and meta.exists() and (d / "docker-compose.yml").exists():
            info = json.loads(meta.read_text())
            info["id"] = d.name
            t[d.name] = info
    return t


def app_dir(name: str) -> Path:
    p = (APPS_DIR / name).resolve()
    if p.parent != APPS_DIR.resolve():
        raise HTTPException(400, "bad app name")
    return p


def project_name(name: str) -> str:
    return f"piboard-{name}"


def compose_args(name: str) -> list[str]:
    d = app_dir(name)
    return ["compose", "--progress", "plain", "--project-directory", str(d), "-f", str(d / "docker-compose.yml"),
            "-p", project_name(name)]


class Job:
    def __init__(self, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.log: list[str] = []
        self.done = False
        self.ok: bool | None = None
        self.started = time.time()


JOBS: dict[str, Job] = {}


def run_job(title: str, steps) -> Job:
    """steps: callable(job) that yields argument lists for `docker` or python callables."""
    job = Job(title)
    JOBS[job.id] = job
    # keep the job table small
    for old in sorted(JOBS.values(), key=lambda j: j.started)[:-20]:
        JOBS.pop(old.id, None)

    def worker():
        try:
            for step in steps:
                if callable(step):
                    msg = step()
                    if msg:
                        job.log.append(msg)
                    continue
                job.log.append("$ docker " + " ".join(step))
                p = subprocess.Popen(["docker", *step], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    job.log.append(line.rstrip())
                    del job.log[:-400]
                if p.wait() != 0:
                    job.log.append(f"!! step failed with exit code {p.returncode}")
                    job.ok = False
                    return
            job.ok = True
        except Exception as e:  # noqa: BLE001
            job.log.append(f"!! {e}")
            job.ok = False
        finally:
            job.done = True

    threading.Thread(target=worker, daemon=True).start()
    return job


def remove_tree(d: Path) -> None:
    """Delete an app folder. Apps that run as root leave root-owned files the
    PiBoard user can't remove, so fall back to a throwaway root container."""
    try:
        shutil.rmtree(d)
        return
    except PermissionError:
        pass
    r = docker("run", "--rm", "--network", "none", "--user", "0", "-v", f"{d}:/w",
               "--entrypoint", "find", SELF_IMAGE, "/w", "-mindepth", "1", "-delete", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    d.rmdir()


def app_states() -> dict[str, str]:
    """project -> aggregated state for compose projects created by PiBoard."""
    r = docker("ps", "-a", "--filter", "label=com.docker.compose.project",
               "--format", '{{.Label "com.docker.compose.project"}}\t{{.State}}')
    states: dict[str, set] = collections.defaultdict(set)
    for line in r.stdout.splitlines():
        if "\t" in line:
            proj, st = line.split("\t", 1)
            states[proj].add(st)
    return {p: ("running" if s == {"running"} else "partial" if "running" in s else "stopped")
            for p, s in states.items()}


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    cpu_sampler.start()
    yield


app = FastAPI(title="PiBoard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

PUBLIC_API = {"/api/health", "/api/status", "/api/login", "/api/setup", "/api/kiosk-login"}
# the kiosk (the Pi's own display) can't even sign itself out; it may only turn its screen off
VIEWER_WRITE_OK: set[str] = {"/api/display/off", "/api/settings"}   # settings: see KIOSK_SETTINGS
# the only settings the Pi display may change: harmless, display-only preferences
KIOSK_SETTINGS = {"screensaver_24h", "screensaver_idle_minutes"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # The raw path the router dispatches on, never request.url.path: request.url is
    # rebuilt from the Host header + path and can disagree with the route that runs.
    path = request.scope["path"]
    if path.startswith("/api/") and path not in PUBLIC_API:
        role = session_role(request)
        if role is None:
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        request.state.role = role
        if request.method not in ("GET", "HEAD"):
            # CSRF: custom header can't be sent cross-site without a CORS preflight,
            # and we never answer preflights. SameSite=Strict cookie is the 2nd layer.
            if request.headers.get("x-piboard") != "1":
                return JSONResponse({"detail": "missing csrf header"}, status_code=403)
            if role != "admin" and path not in VIEWER_WRITE_OK:
                # the Pi display may at most start/restart containers, never stop them
                if not (role == "kiosk" and load_settings()["kiosk_can_control"]
                        and path.startswith("/api/containers/")
                        and path.rsplit("/", 1)[-1] in ("start", "restart")):
                    return JSONResponse({"detail": "read-only session"}, status_code=403)
    elif path in PUBLIC_API and request.method == "POST" and request.headers.get("x-piboard") != "1":
        return JSONResponse({"detail": "missing csrf header"}, status_code=403)

    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # The terminal page alone allows inline <style>: xterm.js injects its own row styles.
    # Scripts stay 'self' everywhere.
    style = "'self' 'unsafe-inline'" if path == "/terminal.html" else "'self'"
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; img-src 'self' data: blob:; style-src {style}; "
        "script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- auth -----------------------------------------------------------------
class PasswordIn(BaseModel):
    password: str


class SetupIn(BaseModel):
    code: str
    password: str


class ChangePwIn(BaseModel):
    current: str
    new: str


class TokenIn(BaseModel):
    token: str


def _check_strength(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(400, "Password must be at least 10 characters.")
    if len(pw) > 256:
        raise HTTPException(400, "Password too long.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status(request: Request):
    return {"configured": is_configured(), "role": session_role(request)}


@app.post("/api/setup")
def setup(body: SetupIn, request: Request, response: Response):
    ip = client_ip(request)
    rate_limit_check(ip)
    with _lock:
        if is_configured():
            raise HTTPException(409, "Already configured.")
        try:
            code = SETUP_CODE_FILE.read_text().strip()
        except OSError:
            raise HTTPException(500, "Setup code missing; restart the container.")
        if not hmac.compare_digest(body.code.strip().upper().encode(), code.encode()):
            rate_limit_fail(ip)
            raise HTTPException(403, "Wrong setup code.")
        _check_strength(body.password)
        _write_json(AUTH_FILE, {"hash": hash_password(body.password), "updated": time.time()})
        SETUP_CODE_FILE.unlink(missing_ok=True)
    set_session_cookie(response, create_session("admin", request.headers.get("user-agent", ""), client_ip(request)))
    return {"ok": True}


@app.post("/api/login")
def login(body: PasswordIn, request: Request, response: Response):
    ip = client_ip(request)
    rate_limit_check(ip)
    stored = _read_json(AUTH_FILE, {}).get("hash")
    if not stored:
        raise HTTPException(409, "Not configured yet.")
    if not verify_password(body.password, stored):
        rate_limit_fail(ip)
        raise HTTPException(401, "Wrong password.")
    rate_limit_reset(ip)
    set_session_cookie(response, create_session("admin", request.headers.get("user-agent", ""), client_ip(request)))
    return {"ok": True}


@app.post("/api/kiosk-login")
def kiosk_login(body: TokenIn, request: Request, response: Response):
    # A valid 256-bit token is always accepted, so failed password guesses
    # (which all share the docker-proxy source IP) can't lock the kiosk out.
    ip = client_ip(request)
    try:
        expected = KIOSK_TOKEN_FILE.read_text().strip()
    except OSError:
        raise HTTPException(500, "kiosk token missing")
    if not hmac.compare_digest(body.token.encode(), expected.encode()):
        rate_limit_check(ip)
        rate_limit_fail(ip)
        raise HTTPException(401, "bad kiosk token")
    set_session_cookie(response, create_session("kiosk"))
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    drop_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/api/password")
def change_password(body: ChangePwIn, request: Request, response: Response):
    ip = client_ip(request)
    rate_limit_check(ip)
    if not verify_password(body.current, _read_json(AUTH_FILE, {}).get("hash", "")):
        rate_limit_fail(ip)
        raise HTTPException(401, "Current password is wrong.")
    _check_strength(body.new)
    _write_json(AUTH_FILE, {"hash": hash_password(body.new), "updated": time.time()})
    drop_all_sessions()  # log out every other device
    set_session_cookie(response, create_session("admin", request.headers.get("user-agent", ""), client_ip(request)))
    return {"ok": True}


# ---- connected devices ----------------------------------------------------
@app.get("/api/sessions")
def list_sessions(request: Request):
    me = _tok_hash(request.cookies.get(SESSION_COOKIE) or "")
    with _lock:
        s = _load_sessions()
    out = [{"id": session_id(h), "role": v.get("role"), "device": v.get("device") or "Browser (signed in earlier)",
            "name": None if v.get("role") == "kiosk" else tailnet_name(v.get("ip")),
            "ip": v.get("ip") or None, "created": v.get("created"), "seen": v.get("seen"), "current": h == me}
           for h, v in s.items()]
    out.sort(key=lambda d: (d["role"] != "kiosk", not d["current"], -(d["seen"] or 0)))
    # one Pi display: older kiosk sessions (from before a reboot) are hidden
    first_kiosk = next((d for d in out if d["role"] == "kiosk"), None)
    return [d for d in out if d["role"] != "kiosk" or d is first_kiosk]


@app.post("/api/sessions/{sid}/revoke")
def revoke_session(sid: str):
    # admin only (middleware). The Pi display (kiosk) can't be signed out.
    if not re.fullmatch(r"[0-9a-f]{16}", sid):
        raise HTTPException(400, "bad id")
    with _lock:
        s = _load_sessions()
        hits = [h for h in s if session_id(h) == sid]
        if not hits:
            raise HTTPException(404, "That device is already signed out.")
        if any(s[h].get("role") == "kiosk" for h in hits):
            raise HTTPException(400, "The Pi display can't be signed out.")
        for h in hits:
            s.pop(h)
        _write_json(SESSIONS_FILE, s)
    return {"ok": True}


# ---- Pi display + USB drives (applied on the host by kiosk/hostd.py) --------
def host_request(action: str, **extra) -> str:
    HOST_REQ_DIR.mkdir(mode=0o700, exist_ok=True)
    rid = f"{time.time():.3f}-{secrets.token_hex(4)}"
    _write_json(HOST_REQ_DIR / f"{rid}.json", {"id": rid, "action": action, **extra})
    return rid


def host_status() -> dict:
    st = _read_json(HOST_STATUS, {})
    st["helper"] = bool(st) and time.time() - st.get("updated", 0) < 150
    return st


@app.get("/api/display")
def get_display():
    st, s = host_status(), load_settings()
    d = st.get("display") or {}
    return {"on": d.get("on", True), "since": d.get("since"), "reason": d.get("reason"), "helper": st["helper"],
            "brightness": s["display_brightness"], "off_at": s["display_off_at"], "on_at": s["display_on_at"]}


@app.post("/api/display/{state}")
def set_display(state: str, request: Request):
    if state not in ("on", "off"):
        raise HTTPException(400, "bad request")
    # middleware: the kiosk may only reach /off; /on is admin only
    if not host_status()["helper"]:
        raise HTTPException(503, "The display helper isn't running. Restart the kiosk: sudo systemctl restart piboard-kiosk")
    host_request("display_" + state, by="pi" if request.state.role == "kiosk" else "admin")
    return {"ok": True}


@app.get("/api/storage")
def get_storage():
    st = host_status()
    out = []
    for d in st.get("drives") or []:
        mp = d.get("mountpoint")
        item = {"name": d.get("name"), "label": d.get("label") or "USB drive", "fstype": d.get("fstype"),
                "size": d.get("size"), "mounted": bool(mp), "path": mp}
        if mp and Path(mp).resolve().is_relative_to(MEDIA_DIR):
            try:
                v = os.statvfs(mp)
                item.update(total=v.f_blocks * v.f_frsize, free=v.f_bavail * v.f_frsize)
            except OSError:
                pass
        out.append(item)
    return {"helper": st["helper"], "drives": out}


@app.post("/api/storage/{name}/eject")
def eject_drive(name: str):
    if not DRIVE_RE.match(name):
        raise HTTPException(400, "bad drive")
    if not host_status()["helper"]:
        raise HTTPException(503, "The storage helper isn't running. Restart the kiosk: sudo systemctl restart piboard-kiosk")
    rid = host_request("eject", drive=name)
    for _ in range(40):                       # wait up to ~20 s for the result
        time.sleep(.5)
        res = (host_status().get("results") or {}).get(rid)
        if res:
            if not res.get("ok"):
                raise HTTPException(409, res.get("error") or "Couldn't eject the drive.")
            return {"ok": True}
    raise HTTPException(504, "The drive is taking long to eject. Check again in a moment.")


# ---- stats / settings -----------------------------------------------------
@app.get("/api/stats")
def api_stats():
    return stats()


@app.get("/api/settings")
def get_settings():
    return settings_out(load_settings())


def settings_out(s: dict) -> dict:
    # the lock screen works out local sunrise/sunset from this (WEATHER_LAT/LON in .env)
    return {**s, "location": {"lat": WEATHER_LAT, "lon": WEATHER_LON}}


@app.put("/api/settings")
def put_settings(body: dict, request: Request):
    if request.state.role != "admin" and not set(body) <= KIOSK_SETTINGS:
        raise HTTPException(403, "read-only session")
    s = load_settings()
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[k]
        if isinstance(default, str):      # "HH:MM" or "" (off)
            v = str(v or "").strip()
            if v and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                raise HTTPException(400, f"{k} must be a time like 00:30")
            s[k] = v
        elif isinstance(default, bool):
            s[k] = bool(v)
        elif isinstance(default, int):
            try:
                s[k] = max(10, min(int(v), 100)) if k == "display_brightness" else max(0, min(int(v), 24 * 60 * 60))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
    _write_json(SETTINGS_FILE, s)
    return settings_out(s)


# ---- containers -----------------------------------------------------------
@app.get("/api/containers")
def api_containers():
    return list_containers()


@app.post("/api/containers/{cid}/{action}")
def container_action(cid: str, action: str):
    if not NAME_RE.match(cid) or action not in ("start", "stop", "restart"):
        raise HTTPException(400, "bad request")
    if action == "stop":
        for c in list_containers():
            if c["self"] and cid in (c["id"], c["name"]):
                raise HTTPException(400, "Refusing to stop PiBoard itself (use restart).")
    r = docker(action, cid, timeout=90)
    if r.returncode != 0:
        raise HTTPException(502, r.stderr.strip()[:500] or "docker error")
    return {"ok": True}


@app.get("/api/containers/{cid}/logs")
def container_logs(cid: str):
    if not NAME_RE.match(cid):
        raise HTTPException(400, "bad id")
    r = subprocess.run(["docker", "logs", "--tail", "100", "--timestamps", cid],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       errors="replace", timeout=20)
    if r.returncode != 0:
        raise HTTPException(502, r.stdout.strip()[:500] or "docker error")
    return {"lines": r.stdout.splitlines()[-100:]}


# ---- app store ------------------------------------------------------------
@app.get("/api/apps")
def api_apps():
    states = app_states()
    out = []
    for name, info in load_templates().items():
        installed = (app_dir(name) / "docker-compose.yml").exists()
        out.append({**info, "installed": installed,
                    "state": states.get(project_name(name), "stopped") if installed else None,
                    "path": str(app_dir(name))})
    return out


@app.post("/api/apps/{name}/install")
def install_app(name: str):
    tpls = load_templates()
    if name not in tpls:
        raise HTTPException(404, "unknown app")
    if not APP_ENV["TAILSCALE_IP"]:
        raise HTTPException(500, "TAILSCALE_IP not set in PiBoard's .env")
    info = tpls[name]
    d = app_dir(name)

    def prepare():
        d.mkdir(parents=True, exist_ok=True)
        for sub in info.get("dirs", []):
            sp = (d / sub).resolve()
            if d in sp.parents:
                sp.mkdir(parents=True, exist_ok=True)
        target = d / "docker-compose.yml"
        if target.exists():
            msg = "Keeping existing docker-compose.yml (delete it to get the template again)"
        else:
            shutil.copy(TEMPLATES_DIR / name / "docker-compose.yml", target)
            msg = f"Copied template to {target}"
        env = "".join(f"{k}={v}\n" for k, v in APP_ENV.items())
        (d / ".env").write_text("# Generated by PiBoard on install\n" + env)
        return msg

    job = run_job(f"Install {info['title']}", [prepare, [*compose_args(name), "pull"],
                                               [*compose_args(name), "up", "-d"]])
    return {"job": job.id}


@app.post("/api/apps/{name}/{action}")
def app_action(name: str, action: str, remove_data: bool = False):
    tpls = load_templates()
    if name not in tpls or action not in ("start", "stop", "update", "uninstall"):
        raise HTTPException(404, "unknown app/action")
    d = app_dir(name)
    if not (d / "docker-compose.yml").exists():
        raise HTTPException(409, "not installed")
    title = tpls[name]["title"]
    if action == "start":
        steps = [[*compose_args(name), "up", "-d"]]
    elif action == "stop":
        steps = [[*compose_args(name), "stop"]]
    elif action == "update":
        steps = [[*compose_args(name), "pull"], [*compose_args(name), "up", "-d"]]
    else:
        def cleanup():
            if remove_data:
                remove_tree(d)
                return f"Deleted {d}"
            (d / "docker-compose.yml").rename(d / "docker-compose.yml.uninstalled")
            return f"Containers removed; data kept in {d}"
        steps = [[*compose_args(name), "down"], cleanup]
    job = run_job(f"{action.capitalize()} {title}", steps)
    return {"job": job.id}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    return {"id": job.id, "title": job.title, "done": job.done, "ok": job.ok, "log": job.log[-200:]}


# ---- screensaver ----------------------------------------------------------
@app.get("/api/screensaver/images")
def screensaver_images():
    if not SCREENSAVER_DIR.is_dir():
        return []
    return sorted(p.name for p in SCREENSAVER_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))


@app.get("/api/screensaver/images/{fname}")
def screensaver_image(fname: str):
    p = (SCREENSAVER_DIR / fname).resolve()
    if p.parent != SCREENSAVER_DIR.resolve() or p.suffix.lower() not in IMAGE_EXT or not p.is_file():
        raise HTTPException(404, "not found")
    resp = FileResponse(p)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---- weather (Open-Meteo, no API key) ---------------------------------------
# Fetched server-side (the page's CSP only allows connect-src 'self'), at most
# once per WEATHER_REFRESH seconds, and kept in data/weather.json so the
# screensaver still shows the last reading after a reboot or while offline.
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "24.21"))    # example default; set in .env
WEATHER_LON = float(os.environ.get("WEATHER_LON", "55.74"))
WEATHER_REFRESH = 15 * 60
WEATHER_RETRY = 2 * 60          # after a failed fetch, wait this long before trying again
WEATHER_FILE = DATA_DIR / "weather.json"
_weather_lock = threading.Lock()
_weather_last_try = 0.0


def _fetch_weather() -> dict:
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({
        "latitude": WEATHER_LAT, "longitude": WEATHER_LON,
        "current": "temperature_2m,weather_code,is_day", "timezone": "auto",
    })
    req = urllib.request.Request(f"https://api.open-meteo.com/v1/forecast?{q}",
                                 headers={"User-Agent": "PiBoard"})
    with urllib.request.urlopen(req, timeout=8) as r:
        cur = json.loads(r.read(200_000))["current"]
    return {"temp": float(cur["temperature_2m"]), "code": int(cur["weather_code"]),
            "is_day": bool(cur.get("is_day", 1)), "fetched": int(time.time())}


@app.get("/api/weather")
def api_weather():
    global _weather_last_try
    cached = _read_json(WEATHER_FILE, None)
    now = time.time()
    fresh = cached and now - cached.get("fetched", 0) < WEATHER_REFRESH
    if not fresh and now - _weather_last_try >= WEATHER_RETRY and _weather_lock.acquire(blocking=False):
        try:
            _weather_last_try = now
            cached = _fetch_weather()
            tmp = WEATHER_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cached))
            tmp.replace(WEATHER_FILE)
        except Exception:
            pass                         # offline / API down: serve the last good reading
        finally:
            _weather_lock.release()
    if not cached:
        raise HTTPException(503, "weather unavailable")
    return {**cached, "age": int(now - cached["fetched"])}


# ---- web terminal (admin only) --------------------------------------------
# A login shell on the Pi, as the PiBoard user (not root), served by kiosk/hostd.py
# over data/term.sock. To open one: an admin session cookie, the admin password typed
# again (one-time ticket, 30 s), and a WebSocket from PiBoard's own origin.
TERM_SOCK = DATA_DIR / "term.sock"
TICKET_TTL = 30
_term_tickets: dict[str, tuple[float, str]] = {}


class TicketIn(BaseModel):
    password: str


@app.post("/api/terminal/ticket")
def terminal_ticket(body: TicketIn, request: Request):
    if request.state.role != "admin":                 # (the middleware already blocks the kiosk)
        raise HTTPException(403, "admin only")
    ip = client_ip(request)
    rate_limit_check(ip)
    if not verify_password(body.password, _read_json(AUTH_FILE, {}).get("hash", "")):
        rate_limit_fail(ip)
        raise HTTPException(401, "Wrong password.")
    rate_limit_reset(ip)
    if not TERM_SOCK.exists() or not host_status()["helper"]:
        raise HTTPException(503, "The terminal helper isn't running. Restart the kiosk: sudo systemctl restart piboard-kiosk")
    now = time.time()
    with _lock:
        for t in [t for t, (exp, _) in _term_tickets.items() if exp < now]:
            _term_tickets.pop(t)
        ticket = secrets.token_urlsafe(24)
        _term_tickets[ticket] = (now + TICKET_TTL, _tok_hash(request.cookies.get(SESSION_COOKIE) or ""))
    return {"ticket": ticket}


def _frame(typ: bytes, payload: bytes) -> bytes:
    return typ + struct.pack(">I", len(payload)) + payload


@app.websocket("/api/terminal/ws")
async def terminal_ws(ws: WebSocket):
    # HTTP middleware doesn't run for WebSockets: every check happens here.
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    if not host or origin not in (f"http://{host}", f"https://{host}"):
        await ws.close(code=1008)
        return
    if session_role(ws) != "admin":
        await ws.close(code=1008)
        return
    me = _tok_hash(ws.cookies.get(SESSION_COOKIE) or "")
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 10)
        with _lock:
            exp, owner = _term_tickets.pop(str(hello.get("ticket", "")), (0, ""))
        if exp < time.time() or not hmac.compare_digest(owner, me):
            await ws.close(code=1008, reason="ticket")
            return
        reader, writer = await asyncio.open_unix_connection(str(TERM_SOCK))
    except (asyncio.TimeoutError, OSError, ValueError, WebSocketDisconnect):
        try:
            await ws.close(code=1011)
        except (RuntimeError, WebSocketDisconnect):
            pass
        return
    with _lock:
        who = (_load_sessions().get(me) or {}).get("device", "admin")
    who = f"{tailnet_name(client_ip(ws)) or who} ({client_ip(ws)})"
    writer.write(_frame(b"r", json.dumps({"cols": hello.get("cols", 80), "rows": hello.get("rows", 24), "who": who}).encode()))
    await writer.drain()
    print(f"terminal opened by {who}", flush=True)

    async def pty_to_ws():
        while True:
            hdr = await reader.readexactly(5)
            data = await reader.readexactly(struct.unpack(">I", hdr[1:])[0])
            if hdr[:1] == b"x":
                return
            await ws.send_bytes(data)

    async def ws_to_pty():
        while True:
            msg = await ws.receive_json()
            if "d" in msg:
                writer.write(_frame(b"d", str(msg["d"]).encode()[:65536]))
            elif "r" in msg:
                cols, rows = msg["r"]
                writer.write(_frame(b"r", json.dumps({"cols": cols, "rows": rows}).encode()))
            await writer.drain()

    tasks = [asyncio.create_task(pty_to_ws()), asyncio.create_task(ws_to_pty())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)   # either side closing ends both
        writer.close()
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):   # the browser closed first
            pass
        print(f"terminal closed ({who})", flush=True)


# ---- static frontend ------------------------------------------------------
@app.get("/kiosk")
def kiosk_page():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
