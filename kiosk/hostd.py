#!/usr/bin/env python3
"""PiBoard host helper: the few things the container can't (and shouldn't) do.

Started by session.sh inside the kiosk's cage session, as the kiosk user. Being in
the active seat session is what lets it:
  * switch the HDMI output off/on (wlr-randr, Wayland), on request, on a daily
    schedule (default off at 00:30) and back on when the touchscreen is tapped;
  * mount USB drives as they are plugged in (udisks2: /media/<user>/<label>),
    and safely eject them on request;
  * serve the admin web terminal: a login shell as this user (not root) on a pty,
    over a private unix socket (data/term.sock, mode 600) that only PiBoard uses.
    PiBoard checks the admin session and asks for the password again first.

It talks to PiBoard only through files in data/ (mode 700, owned by this user):
  data/host-requests/*.json   requests from PiBoard, deleted once handled
  data/host-status.json       what is true now (display, drives), for PiBoard to show
  data/settings.json          read only: display_off_at / display_on_at
No network, no root, no new privileges for the container.
"""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import socket
import termios
import threading
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REQ_DIR = DATA / "host-requests"
STATUS = DATA / "host-status.json"
SETTINGS = DATA / "settings.json"
LOG = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "piboard-hostd.log"

TERM_SOCK = DATA / "term.sock"
TERM_MAX = 4                 # concurrent terminals
TERM_IDLE = 30 * 60          # close a terminal after 30 min without input

EV_KEY, BTN_TOUCH = 1, 330
EVENT = struct.Struct("llHHi")        # struct input_event on 64-bit Linux


def log(msg: str) -> None:
    try:
        with LOG.open("a") as f:
            f.write(time.strftime("%F %T ") + msg + "\n")
    except OSError:
        pass


def run(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- display
class Display:
    def __init__(self) -> None:
        self.output = self._detect_output()
        self.on = self._query() is not False
        self.since = time.time()
        self.reason = "start"

    @staticmethod
    def _detect_output() -> str | None:
        try:
            r = run("wlr-randr")
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in r.stdout.splitlines():
            if line and not line[0].isspace():
                return line.split()[0]
        return None

    def _query(self) -> bool | None:
        if not self.output:
            return None
        try:
            r = run("wlr-randr")
        except (OSError, subprocess.TimeoutExpired):
            return None
        block = r.stdout.split(self.output, 1)[-1]
        for line in block.splitlines():
            if line.strip().startswith("Enabled:"):
                return line.split(":", 1)[1].strip() == "yes"
        return None

    def set(self, on: bool, reason: str) -> None:
        if not self.output:
            self.output = self._detect_output()
            if not self.output:
                log("no display output found")
                return
        if on == self.on:
            return
        try:
            r = run("wlr-randr", "--output", self.output, "--on" if on else "--off")
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"wlr-randr failed: {e}")
            return
        if r.returncode == 0:
            self.on, self.since, self.reason = on, time.time(), reason
            log(f"display {'on' if on else 'off'} ({reason})")
        else:
            log(f"wlr-randr: {r.stderr.strip()[:200]}")


def find_touch() -> str | None:
    for name_file in sorted(Path("/sys/class/input").glob("event*/device/name")):
        try:
            name = name_file.read_text().strip().lower()
        except OSError:
            continue
        if "touch" in name or "ads7846" in name:
            return "/dev/input/" + name_file.parent.parent.name
    return None


def parse_hhmm(v) -> int | None:
    """'00:30' -> minutes since midnight; '' / bad -> None (disabled)."""
    try:
        h, m = str(v).split(":")
        h, m = int(h), int(m)
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except ValueError:
        return None


def crossed(prev: float, now: float, minute: int | None) -> bool:
    """Did local wall-clock time pass HH:MM (given as minutes) between prev and now?"""
    if minute is None or now - prev > 6 * 3600:      # ignore huge jumps (clock set at boot)
        return False
    t = datetime.fromtimestamp(now).replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0).timestamp()
    return prev < t <= now or prev < t - 86400 <= now


# ---------------------------------------------------------------- storage
def usb_partitions() -> list[dict]:
    try:
        r = run("lsblk", "-J", "-b", "-o", "NAME,PATH,TRAN,FSTYPE,LABEL,UUID,SIZE,MOUNTPOINT,TYPE")
        tree = json.loads(r.stdout or "{}").get("blockdevices", [])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    out = []
    for disk in tree:
        if disk.get("tran") != "usb":
            continue
        for part in disk.get("children") or [disk]:
            if part.get("fstype") and part.get("fstype") not in ("swap", "crypto_LUKS"):
                out.append({"name": part["name"], "path": part["path"], "disk": disk["path"],
                            "fstype": part["fstype"], "label": part.get("label") or "",
                            "size": int(part.get("size") or 0), "mountpoint": part.get("mountpoint")})
    return out


class Storage:
    def __init__(self) -> None:
        self.ejected: set[str] = set()     # partitions the user ejected: don't remount until replugged
        self.failed: dict[str, float] = {}

    def tick(self) -> list[dict]:
        parts = usb_partitions()
        present = {p["name"] for p in parts}
        self.ejected &= present             # replugged (or gone): forget
        for p in parts:
            if p["mountpoint"] or p["name"] in self.ejected:
                continue
            if time.time() - self.failed.get(p["name"], 0) < 60:
                continue
            r = run("udisksctl", "mount", "-b", p["path"], "--no-user-interaction", timeout=60)
            if r.returncode == 0:
                log(f"mounted {p['path']}: {r.stdout.strip()}")
            else:
                self.failed[p["name"]] = time.time()
                log(f"mount {p['path']} failed: {r.stderr.strip()[:200]}")
        return usb_partitions() if any(not p["mountpoint"] for p in parts) else parts

    def eject(self, name: str) -> str:
        for p in usb_partitions():
            if p["name"] == name:
                if p["mountpoint"]:
                    r = run("udisksctl", "unmount", "-b", p["path"], "--no-user-interaction", timeout=60)
                    if r.returncode != 0:
                        return r.stderr.strip()[:200] or "unmount failed (is it in use?)"
                self.ejected.add(name)
                # power the drive off when no other partition on it is still mounted
                if not any(q["disk"] == p["disk"] and q["mountpoint"] for q in usb_partitions()):
                    run("udisksctl", "power-off", "-b", p["disk"], "--no-user-interaction", timeout=30)
                log(f"ejected {p['path']}")
                return ""
        return "drive not found"


# ---------------------------------------------------------------- web terminal
# Frames on the socket: 1 byte type + 4 byte big-endian length + payload.
#   b"d" data (both ways)   b"r" resize {"cols","rows"} (from PiBoard)   b"x" exit (to PiBoard)
def _send(conn: socket.socket, typ: bytes, payload: bytes) -> None:
    conn.sendall(typ + struct.pack(">I", len(payload)) + payload)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _recv_frame(conn: socket.socket) -> tuple[bytes, bytes]:
    hdr = _recv_exact(conn, 5)
    n = struct.unpack(">I", hdr[1:])[0]
    if n > 1 << 20:
        raise ConnectionError("frame too large")
    return hdr[:1], _recv_exact(conn, n)


def _winsize(fd: int, payload: bytes) -> None:
    try:
        size = json.loads(payload)
        rows, cols = max(2, min(int(size["rows"]), 500)), max(10, min(int(size["cols"]), 500))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (ValueError, KeyError, TypeError, OSError):
        pass


def term_session(conn: socket.socket) -> None:
    pid = fd = None
    try:
        conn.settimeout(10)
        typ, payload = _recv_frame(conn)          # first frame: the initial size
        if typ != b"r":
            return
        who = str(json.loads(payload).get("who", "?"))[:80]
        home = os.path.expanduser("~")
        pid, fd = pty.fork()
        if pid == 0:                               # child: a clean login shell
            os.chdir(home)
            env = {"HOME": home, "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("USER", ""),
                   "SHELL": "/bin/bash", "TERM": "xterm-256color", "COLORTERM": "truecolor",
                   "LANG": os.environ.get("LANG", "C.UTF-8"),
                   "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
            os.execvpe("/bin/bash", ["bash", "-l"], env)
        _winsize(fd, payload)
        log(f"terminal opened by {who} (pid {pid})")
        conn.settimeout(None)
        last_input = time.time()
        while True:
            ready = select.select([conn, fd], [], [], 30)[0]
            if not ready and time.time() - last_input > TERM_IDLE:
                _send(conn, b"d", b"\r\n[PiBoard] Closed after 30 minutes without input.\r\n")
                break
            if fd in ready:
                try:
                    out = os.read(fd, 65536)
                except OSError:                    # EIO: the shell exited
                    break
                if not out:
                    break
                _send(conn, b"d", out)
            if conn in ready:
                typ, payload = _recv_frame(conn)
                if typ == b"d":
                    os.write(fd, payload)
                    last_input = time.time()
                elif typ == b"r":
                    _winsize(fd, payload)
    except (OSError, ConnectionError, ValueError) as e:
        if not isinstance(e, ConnectionError):
            log(f"terminal: {e!r}")
    finally:
        try:
            _send(conn, b"x", b"")
        except OSError:
            pass
        conn.close()
        if fd is not None:
            os.close(fd)
        if pid:
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass
            for _ in range(20):
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
                time.sleep(.1)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
            log(f"terminal closed (pid {pid})")


def term_server() -> None:
    try:
        TERM_SOCK.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)                          # socket mode 600: this user only
    try:
        srv.bind(str(TERM_SOCK))
    finally:
        os.umask(old)
    srv.listen(8)
    sessions: list[threading.Thread] = []
    while True:
        conn, _ = srv.accept()
        sessions = [t for t in sessions if t.is_alive()]
        if len(sessions) >= TERM_MAX:
            try:
                _send(conn, b"d", b"[PiBoard] Too many terminals are open. Close one and try again.\r\n")
                _send(conn, b"x", b"")
            finally:
                conn.close()
            continue
        t = threading.Thread(target=term_session, args=(conn,), daemon=True)
        t.start()
        sessions.append(t)


_term_started = False


def start_term_server() -> None:
    global _term_started
    if _term_started:
        return
    _term_started = True

    def run_forever():
        while True:
            try:
                term_server()
            except Exception as e:                 # keep serving no matter what
                log(f"terminal server: {e!r}; restarting in 5 s")
                time.sleep(5)
    threading.Thread(target=run_forever, daemon=True).start()


# ---------------------------------------------------------------- main loop
def main() -> None:
    REQ_DIR.mkdir(mode=0o700, exist_ok=True)
    start_term_server()
    disp, store = Display(), Storage()
    touch_path, touch = None, None
    prev = time.time()
    last_store = 0.0
    drives: list[dict] = []
    results: dict[str, dict] = {}
    last_body, last_write = None, 0.0
    log(f"hostd started (output={disp.output})")
    while True:
        # touch device (re)open: it can appear late after boot
        if touch is None:
            touch_path = find_touch()
            if touch_path:
                try:
                    touch = open(touch_path, "rb", buffering=0)
                    os.set_blocking(touch.fileno(), False)
                except OSError as e:
                    log(f"touch {touch_path}: {e}")
                    touch = None
        ready = select.select([touch], [], [], 1.0)[0] if touch else (time.sleep(1.0) or [])
        if ready:
            try:
                buf = touch.read(EVENT.size * 64) or b""
            except OSError:
                touch.close()
                touch, buf = None, b""
            for i in range(0, len(buf) - EVENT.size + 1, EVENT.size):
                _, _, typ, code, val = EVENT.unpack_from(buf, i)
                if typ == EV_KEY and code == BTN_TOUCH and val == 1 and not disp.on:
                    disp.set(True, "touch")

        # daily schedule
        now = time.time()
        st = read_json(SETTINGS, {})
        off_at, on_at = parse_hhmm(st.get("display_off_at", "00:30")), parse_hhmm(st.get("display_on_at", ""))
        if crossed(prev, now, off_at):
            disp.set(False, "schedule")
        if crossed(prev, now, on_at):
            disp.set(True, "schedule")
        prev = now

        # requests from PiBoard
        for f in sorted(REQ_DIR.glob("*.json")):
            req = read_json(f, {})
            try:
                f.unlink()
            except OSError:
                pass
            rid = str(req.get("id", f.stem))[:40]
            act = req.get("action")
            if act in ("display_on", "display_off"):
                disp.set(act == "display_on", req.get("by", "manual"))
                results[rid] = {"ok": True}
            elif act == "eject":
                err = store.eject(str(req.get("drive", "")))
                results[rid] = {"ok": not err, "error": err}
                last_store = 0
            while len(results) > 20:
                results.pop(next(iter(results)))

        if now - last_store > 3:
            drives = store.tick()
            last_store = now
        # write only on change (plus a heartbeat each minute): spare the SD card
        body = {"display": {"on": disp.on, "since": disp.since, "reason": disp.reason, "output": disp.output},
                "drives": drives, "results": results}
        if body != last_body or now - last_write > 60:
            write_json(STATUS, {"updated": now, **body})
            last_body, last_write = json.loads(json.dumps(body)), now


if __name__ == "__main__":
    # one instance only
    lock = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "piboard-hostd.pid"
    try:
        pid = int(lock.read_text())
        os.kill(pid, 0)
        if Path(f"/proc/{pid}/cmdline").read_bytes().find(b"hostd.py") >= 0:
            sys.exit(0)
    except (OSError, ValueError):
        pass
    lock.write_text(str(os.getpid()))
    while True:
        try:
            main()
        except Exception as e:      # keep the display and drives managed no matter what
            log(f"crash: {e!r}; restarting in 5 s")
            time.sleep(5)
