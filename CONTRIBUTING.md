# Contributing to PiBoard

Thanks for helping. PiBoard is deliberately small: one FastAPI file, plain
HTML/CSS/JS with no build step, and no frontend framework. Please keep it that way.

## Development setup

```bash
git clone https://github.com/shameemkabeer/shaserver.git ~/piboard
cd ~/piboard
./install.sh                 # writes .env, builds and starts the container
docker compose up -d --build # after you change backend/ or frontend/
docker logs -f piboard
```

You can develop on any Linux machine with Docker. Tailscale is optional: set
`TAILSCALE_IP=127.0.0.1` in `.env` if you don't have it. The kiosk parts
(`kiosk/`) need a Raspberry Pi with a display.

## Ground rules

- **Never bind to `0.0.0.0`.** Every published port uses `${LOCAL_IP}` and
  `${TAILSCALE_IP}`. Docker ports bypass ufw (see the README).
- **No secrets in the repo.** Anything secret is generated at runtime in `data/`.
- **No shell invocation** in the backend: pass subprocess arguments as lists and
  validate container IDs and app names.
- **No external requests from the browser.** The CSP is `'self'` only. Vendor
  libraries into `frontend/vendor/` and record version + checksum in `VERSIONS.txt`.
- Keep the UI usable at **800x480** (kiosk) and on a phone.

## Adding an app template

1. Create `templates/<name>/docker-compose.yml` and `templates/<name>/app.json`
   (copy an existing one).
2. Publish ports only as `${LOCAL_IP}:PORT:PORT` and `${TAILSCALE_IP}:PORT:PORT`.
3. Use `${PUID}`/`${PGID}`/`${TZ}` and keep data under the app folder.
4. Test install, start, stop, update and uninstall from the Apps page.

## Pull requests

- One topic per PR, with a short description of what you tested (and on which
  hardware, if it touches the kiosk).
- Run `python3 -m py_compile backend/main.py kiosk/hostd.py` before pushing.
- Security problems: see [SECURITY.md](SECURITY.md) and don't open public issues.
