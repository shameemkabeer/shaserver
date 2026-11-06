# PiBoard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: Raspberry Pi 5](https://img.shields.io/badge/platform-Raspberry%20Pi%205-c51a4a)
![Docker](https://img.shields.io/badge/runs%20in-Docker-2496ed)

A small, self-hosted home-server dashboard for the Raspberry Pi 5 (in the spirit of CasaOS). It's one Docker container: a FastAPI backend and a single-page HTML/JS frontend with no build step and no frameworks.

- **System stats**: CPU, RAM, CPU temperature, disk, uptime and load. Refreshes every 3 s.
- **Containers**: lists every container and its status, with start, stop, restart and the last 100 log lines.
- **App store**: one-click install of AdGuard Home, Uptime Kuma, File Browser and Jellyfin, each into `~/piboard/apps/<name>`.
- **Login**: one admin password (scrypt-hashed, set on first run with a one-time setup code), an HttpOnly session cookie and rate-limited login.
- **UI**: dark, and works on phones, desktops and a 480x320 touchscreen (side icon rail in small landscape).
- **Kiosk**: cage + Chromium full-screen on the HDMI display. Starts at boot, with no desktop.
- **Screensaver**: lock-screen-style clock and date over an animated sky that follows the time of day, with the current weather under the clock. Tap to wake.

```
piboard/
├── docker-compose.yml   Dockerfile   .env (generated)   .env.example
├── install.sh  update.sh  uninstall.sh
├── backend/main.py      FastAPI app (stats, docker, apps, auth)
├── frontend/            index.html, app.js, style.css (Inter font bundled at build)
├── templates/<app>/     built-in compose templates + app.json
├── kiosk/               install-kiosk.sh, start-kiosk.sh, uninstall-kiosk.sh, kiosk.conf.example
├── apps/<name>/         installed apps (compose file, .env, data)     ← runtime
├── data/                password hash, sessions, settings, tokens      ← runtime, mode 700
└── screensaver/         your wallpaper images                          ← you add these
```

## Install

Requirements: Raspberry Pi OS Lite 64-bit (Trixie), Docker with the compose plugin, Tailscale up, and your user in the `docker` group. Other 64-bit Linux machines work too, without the kiosk.

```bash
git clone https://github.com/shameemkabeer/shaserver.git ~/piboard
cd ~/piboard
./install.sh
```

Then set your location for the screensaver's weather and sunrise/sunset times in `.env` (`WEATHER_LAT`, `WEATHER_LON`) and run `docker compose up -d`.

The installer:

1. Writes `.env`. It detects your Tailscale IP, UID/GID, the docker group's GID and your timezone, and never overwrites an existing `.env`.
2. Offers to set `net.ipv4.ip_nonlocal_bind=1` (needs sudo). **Recommended.** If Docker starts before `tailscale0` has its IP, any container published on your Tailscale IP fails to start at boot. This setting lets the bind succeed early. It doesn't expose anything to the LAN.
3. Builds the image, starts it, and prints the **first-run setup code**.

Open `http://<tailscale-ip>:8088` (or `http://<hostname>:8088` with MagicDNS), enter the setup code and choose an admin password of at least 10 characters. The code is also in `~/piboard/data/setup.code` and in `docker logs piboard`. It's deleted once used.

### Kiosk on the HDMI display

```bash
sudo ./kiosk/install-kiosk.sh
sudo systemctl start piboard-kiosk      # or just reboot
journalctl -u piboard-kiosk -f          # troubleshooting
```

This installs `cage` and `chromium` (no desktop environment) and creates `piboard-kiosk.service`. The service takes over tty1 in place of the login prompt, waits for PiBoard, then opens it full-screen. The kiosk signs in with a random token from `data/kiosk.token` and gets a **read-only** session. The Pi display is the server's own screen, so it never shows options that could harm it: no sign-out, no admin sign-in, no stop, update or uninstall, and no password change. The backend refuses these for the kiosk session too. In Settings → Kiosk (from another device) you can let it start and restart containers, but never stop them. Changing the password signs out every device except the Pi display.

**Devices:** Settings → Devices lists every signed-in device by its Tailscale name (for example "my-laptop"), with its browser ("Chrome on Windows"), its address and when it was last active. PiBoard gets the name from a reverse lookup at Tailscale's MagicDNS (100.100.100.100). Devices outside the tailnet show the browser name only. From another device, an admin can sign any browser out except the Pi display.

Tuning goes in `kiosk/kiosk.conf` (copy it from `kiosk.conf.example`): browser zoom, plus extra cage or Chromium flags.

#### Kiosk display (Waveshare 3.5in HDMI LCD)

- **Resolution:** 800x480, the panel's preferred EDID mode (`KIOSK_MODE=""` in `kiosk/kiosk.conf`). It looked better than a forced 480x320 in the display test.
- **Touch:** the XPT2046 on the GPIO header, enabled in `/boot/firmware/config.txt` with `dtoverlay=ads7846,cs=1,penirq=25,...`. Calibration is in `kiosk/touch.conf`, and `sudo ./kiosk/install-kiosk.sh` turns it into `/etc/udev/rules.d/99-piboard-touch.rules`. To recalibrate, set `KIOSK_URL="file:///home/<you>/piboard/kiosk/tools/calibrate.html"`, run `pkill -f piboard-kiosk-browser` and `python3 -I kiosk/tools/calibrate-touch.py`, press and hold the five targets, then clear `KIOSK_URL` and re-run the install script.
- **Cursor:** hidden on the kiosk with CSS. A USB mouse still works, but you won't see the pointer.

### Pi display: power, brightness, nightly off

- **Off button:** the Pi's own screen has a power button in the top bar. Tap the dark screen to turn it back on (that tap doesn't click anything).
- **Admin, from another device: Settings → Pi display.** Turn the screen on or off, set the brightness (10–100 %), and set daily times. By default the screen turns off every night at **00:30** and stays off until tapped. Set *Turn on every morning at* to bring it back automatically.
- **How it works:** off means the HDMI output is switched off (`wlr-randr`), so the panel gets no signal. The panel has no software backlight control, so brightness is a dimming layer drawn over the picture.

### External storage (USB drives)

- Plug a USB drive into the Pi and it is mounted within a few seconds at `/media/<user>/<label>` (udisks2). Home shows it with its free space. An admin can **Eject** it from another device before unplugging.
- Apps installed from now on get the drives too: File Browser at `/srv/usb` (read-write), and Jellyfin at `/usb` (read-only; add `/usb/<user>/<drive>` as a library). For an app you installed earlier, add `- /media:/srv/usb:rslave` to its `volumes:` in `apps/<name>/docker-compose.yml`.

Both are done by `kiosk/hostd.py`, a small helper that `session.sh` starts inside the kiosk session. It runs as you, not root, and it must run in that session so Wayland and udisks allow it. PiBoard talks to it only through files in `data/` (`host-requests/`, `host-status.json`). It writes its log to `/run/user/<uid>/piboard-hostd.log`.

### Web terminal (admin)

**Terminal** in the tab bar (admin only, never on the Pi display) opens a shell on the Pi in your browser, on a phone or a laptop. You can run commands and edit files (`nano`, `vim`). On phones a key bar adds Esc, Tab, Ctrl and arrow keys.

- **Who:** admin sessions only. The **admin password is asked again** for every terminal, so a stolen cookie alone can't open one. The kiosk is refused by the server.
- **What:** a login shell as your user (the one running PiBoard), not root. `sudo` still asks for your Linux password.
- **How:** `kiosk/hostd.py` runs the shell on a pty. PiBoard passes it through a private unix socket (`data/term.sock`, mode 600) to a WebSocket that only accepts PiBoard's own origin.
- **Limits:** at most 4 terminals at once. A terminal closes after 30 minutes without typing. Opens and closes are logged in `docker logs piboard` and `/run/user/<uid>/piboard-hostd.log`.
- **Library:** xterm.js 6.0.0 (MIT), served from `frontend/vendor/`, with versions and checksums in `VERSIONS.txt`. The terminal page alone allows inline styles, which xterm needs. Scripts stay same-origin only.

### Screensaver

- Turn it on per device in **Settings → Use screensaver on this device**. It's on by default on the kiosk.
- The idle timeout is under **Settings → Idle minutes** (0 = off). The default is 2. The Pi display itself may change the idle time and the 24-hour clock (they save as soon as you change them). Every other setting needs the admin account.
- **Sky background (no image files):** generated with CSS/SVG. The scene follows the real sun at your location (`WEATHER_LAT`/`WEATHER_LON` in `.env`). Sunrise and sunset are computed on the Pi, so this works offline and shifts with the seasons. It's the same desert landscape (a rocky massif and dunes, with haze in the valleys) in four kinds of light. *Sunrise* (from 30 min before sunrise, for 3 h) is a soft dawn. *Day* is a deep clear sky. *Sunset* (from 90 min before sunset to 40 min after) is orange to violet. *Night* is the Milky Way with twinkling stars and a crescent moon. Settings shows today's times. At each boundary the scenes crossfade over 5 s. The scene is animated: a slow camera drift over the whole sky, clouds travelling across, the sun rising and setting, and at night stronger twinkling, a slowly turning Milky Way and three shooting stars. A fine film grain and a vignette finish the look. Animations only use transform and opacity. With the screensaver showing, Chromium uses about half of one core, roughly 13% of the Pi 5. The clock uses Inter Display Light. Under it are just the weather icon and °C, with no pill and no condition text. With *Reduce motion* turned on, the sky is still.
- **Preview** (Settings) tours all four skies, about 12 s each, with a label naming each one. Tap to exit.
- **Weather:** the icon and °C under the clock come from [Open-Meteo](https://open-meteo.com) (free, no API key) for your location. The backend fetches it (the page's CSP blocks outside requests) at most every 15 minutes and keeps the last reading in `data/weather.json`. The browser keeps a copy too, so the screensaver still shows the last known weather while offline. A reading older than 3 hours is dimmed. To change the location, set `WEATHER_LAT` / `WEATHER_LON` in `.env` and run `docker compose up -d`.
- **Your own wallpapers (optional):** put `.jpg/.png/.webp/.avif/.gif` files in `~/piboard/screensaver/` and they cover the sky. They change with the time of day (sunrise, day, sunset, night) instead of on a timer. Put `sunrise`, `day`, `sunset` or `night` in a file name to show that image at that time of day. Untagged images take turns, one per time of day. New images are picked up the next time the screensaver starts. Remove them to get the sky back.
- The date (e.g. "Friday, 6 November") sits above a large bold clock, both in Inter. CPU temperature is shown bottom-right. Tap anywhere to fade back. The wake-up tap doesn't click through to the dashboard.

### App store notes

| App | Port(s) | Notes |
|---|---|---|
| AdGuard Home | 3000 (web), 53 (DNS) | In the setup wizard keep the web port at **3000**. Point Tailscale DNS at the Pi's Tailscale IP to use it tailnet-wide. |
| Uptime Kuma | 3001 | Create the admin account on first visit. |
| File Browser | 8082 | User `admin`. The random first-run password is in its logs (Containers → filebrowser → Logs). Serves `apps/filebrowser/files`. |
| Jellyfin | 8096 | Media goes in `apps/jellyfin/media`, or edit the volume. The Pi 5 has no hardware video encoder, so prefer direct play. |

Each app's `docker-compose.yml` is copied into `apps/<name>/` on install and is yours to edit. Reinstalling keeps your edited version. **Uninstall** offers to keep or delete the data.

## Update

```bash
cd ~/piboard && ./update.sh
```

This runs `git pull` if the folder is a git checkout, rebuilds with fresh base images and restarts. Your password, settings and apps in `data/` and `apps/` are untouched. To update installed apps, use **Update** on the Apps page, which runs `compose pull` and `up -d`.

## Uninstall

```bash
./uninstall.sh            # remove PiBoard (and the kiosk service); apps keep running, data kept
./uninstall.sh --apps     # also remove all PiBoard-installed app containers (data kept)
./uninstall.sh --purge    # --apps + delete apps/ and data/ (asks you to type 'delete')
```

The kiosk can be removed on its own with `sudo ./kiosk/uninstall-kiosk.sh`. If you accepted the sysctl during install, remove it with `sudo rm /etc/sysctl.d/99-piboard.conf`.

## Security

### Network exposure: Docker bypasses ufw

Docker writes its own iptables rules for published ports, and those rules take effect **before ufw**. A port published on `0.0.0.0` is reachable from your LAN (and the internet, if forwarded) even when ufw only allows `tailscale0`. So:

- PiBoard publishes only on `127.0.0.1:8088` and `<tailscale-ip>:8088`.
- Every app template publishes only on `${LOCAL_IP}` (127.0.0.1) and `${TAILSCALE_IP}`. PiBoard writes both into each app's `.env`.
- If you add your own containers, always write `127.0.0.1:PORT:PORT` / `<tailscale-ip>:PORT:PORT`, never a bare `PORT:PORT`.

To check what's listening: `sudo ss -tlnup | grep -v -E '127.0.0.1|<tailscale-ip>'`. Docker ports should never appear in that output.

### The Docker socket is root access

PiBoard mounts `/var/run/docker.sock` so it can manage containers. **Anyone who controls the socket controls the host as root.** For example, they could start a privileged container that mounts `/`. In practice:

- A PiBoard admin session is effectively root on the Pi. Use a strong, unique password.
- A remote-code-execution bug in PiBoard would also be root. The attack surface is kept small: no shell is ever invoked, container IDs and app names are validated, and arguments are passed as lists.
- The container is hardened as far as the socket allows. It runs as your UID (not root), with a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`, and only the folders it needs are mounted.
- PiBoard is reachable only on localhost and your tailnet. Don't publish it more widely. If you share your tailnet, restrict port 8088 with Tailscale ACLs.
- A socket proxy (e.g. tecnativa/docker-socket-proxy) **doesn't help much here**, because installing apps through compose needs create and run rights, which are root-equivalent anyway.

### Secrets and sessions

- No secrets are in the repo, the image or `.env`. At first start PiBoard generates the one-time setup code and the kiosk token in `data/` (mode 600). The password is stored as a salted **scrypt** hash in `data/auth.json`.
- Session tokens are random, 256-bit, and kept only as SHA-256 hashes in `data/sessions.json`. They expire after 30 days. Changing the password signs out every session.
- The cookie is `HttpOnly` and `SameSite=Strict`. Every state-changing request also needs a custom header (CSRF protection). Strict CSP and `X-Frame-Options: DENY` are set.
- **Login rate limit:** 5 failures per 15 min per source IP and 20 per 15 min globally, then HTTP 429. Behind Docker's userland proxy all clients can share one source IP, which is why the global cap exists. A valid kiosk token is never locked out.
- Plain HTTP is used inside the tailnet, and WireGuard already encrypts that traffic. For HTTPS, use `tailscale serve --bg 8088` (it proxies to 127.0.0.1:8088).
- The kiosk token appears on Chromium's command line, so other local users on the Pi can see it with `ps`. It only grants a read-only session. Rotate it with `rm data/kiosk.token && docker restart piboard && sudo systemctl restart piboard-kiosk`.

## Troubleshooting

- **Forgot the password:** `rm ~/piboard/data/auth.json && docker restart piboard`, then use the new setup code from `docker logs piboard`.
- **Container fails at boot with "cannot assign requested address":** Docker started before Tailscale. Enable the sysctl (see Install).
- **Stats look wrong:** values come from the host's `/proc` and `/sys/class/thermal`. Disk usage is the filesystem holding `~/piboard`.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

PiBoard is released under the [MIT License](LICENSE). Bundled third-party code: [xterm.js](https://github.com/xtermjs/xterm.js) (MIT, `frontend/vendor/`) and the [Inter](https://rsms.me/inter/) font (SIL OFL 1.1, downloaded at build time).
