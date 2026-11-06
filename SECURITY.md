# Security policy

PiBoard manages Docker through the Docker socket, so a PiBoard admin session is
effectively root on the host. Security bugs matter. Please report them privately.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** (Security tab → *Report a
vulnerability*) on this repository. Please don't open a public issue for security
problems.

Include the PiBoard commit you tested, what an attacker needs (tailnet access?
a kiosk session? an admin cookie?) and steps to reproduce. You should get a reply
within a week.

## Scope and design assumptions

- PiBoard is meant to be reachable **only** on `127.0.0.1` and a Tailscale IP. Issues
  that only happen when it's published on `0.0.0.0` or the internet are still welcome,
  but are lower priority.
- In scope: authentication and session bypass, CSRF, XSS, escaping from the read-only
  kiosk session, path traversal in the app store or file handling, command injection,
  and the web terminal.
- Out of scope: an admin using the Docker socket to gain root (that's by design, see
  the README section *The Docker socket is root access*).
