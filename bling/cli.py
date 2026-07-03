"""bling command line: login, har, open, run, shell, play.

bling login
bling har demo.browserling.com --out demo.har
bling open example.com --browser firefox
bling open https://app.example.com/login --keep-open   # attended session
bling run "whoami"
bling shell --record login.bling    # interactive REPL, records a .bling file
bling play login.bling              # replay a recording unattended
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import Session, __version__, har, login
from .errors import BlingError
from .shell import run_shell


def _host(url: str) -> str:
    """Best-effort hostname for naming the output file; never raises on junk input."""
    t = url if "//" in url else "http://" + url
    try:
        return urlparse(t).hostname or "capture"
    except ValueError:
        return "capture"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bling", description="Drive a Browserling sandbox.")
    ap.add_argument("--version", action="version", version=f"bling {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="one-time interactive login (you solve the CAPTCHA)")

    p = sub.add_parser("har", help="capture a URL's HAR")
    p.add_argument("url")
    p.add_argument("--out", default=None, help="output .har (default: <host>.har)")
    p.add_argument("--os", default="win10")
    p.add_argument("--live", action="store_true", help="show the browser window")

    p = sub.add_parser("open", help="open a session, wait ready, screenshot (or keep open)")
    p.add_argument("url")
    p.add_argument("--os", default="win10")
    p.add_argument("--browser", default="chrome138")
    p.add_argument("--live", action="store_true", help="show the browser window")
    p.add_argument(
        "-k",
        "--keep-open",
        action="store_true",
        help="leave the session open until you press Enter / Ctrl-C (implies --live)",
    )
    p.add_argument(
        "--proxy",
        choices=("datacenter", "residential", "mobile", "tor"),
        default=None,
        help="route the session through a proxy/VPN of this kind",
    )
    p.add_argument(
        "--country",
        default=None,
        help="proxy exit country (must match Browserling's dropdown label, e.g. 'United States')",
    )

    p = sub.add_parser("run", help="run a shell command in the VM")
    p.add_argument("command")
    p.add_argument("--os", default="win10")
    p.add_argument("--browser", default="chrome138")
    p.add_argument("--live", action="store_true", help="show the browser window")

    p = sub.add_parser("shell", help="interactive REPL that drives a session (record/replay)")
    p.add_argument("--record", metavar="FILE", help="record typed commands to FILE (.bling)")
    p.add_argument("--play", metavar="FILE", help="replay FILE first, then drop to the prompt")
    p.add_argument("--headless", action="store_true", help="run without showing the browser window")

    p = sub.add_parser("play", help="replay a .bling recording unattended (no prompt, no TTY)")
    p.add_argument("file")
    p.add_argument("--live", action="store_true", help="show the browser window during replay")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "login":
            login()
            return 0
        if args.cmd == "har":
            out = args.out or _host(args.url) + ".har"
            h = har(args.url, out=out, os=args.os, live=args.live)
            print(f"OK: {out} ({len(h)} entries, creator {h.creator})")
            return 0
        if args.cmd == "open":
            live = args.live or args.keep_open  # keep-open is useless headless
            with Session(headless=not live) as s:
                s.require_login()
                state = s.open(args.url, os=args.os, browser=args.browser)
                if args.proxy:
                    print(f"setting {args.proxy} proxy"
                          + (f" (country: {args.country})" if args.country else "")
                          + " ...")
                    s.set_proxy(args.proxy, country=args.country)
                    s.wait_ready(timeout=45)  # re-route restarts the VM stream
                if args.keep_open:
                    print(f"{state}: {args.url} on {args.os}/{args.browser}")
                    print("session is open — close the browser window (or Ctrl-C here) to end it")
                    # Poll the outer page; closing the window flips is_closed() True. Robust
                    # in any context (TTY, pipe, background) — no stdin required.
                    try:
                        while not s.page.is_closed():
                            time.sleep(0.5)
                    except KeyboardInterrupt:
                        pass
                else:
                    shot = s.screenshot(Path("_explore/session.png"))
                    print(f"{state}: {args.url} on {args.os}/{args.browser} -> {shot}")
            return 0
        if args.cmd == "run":
            with Session(headless=not args.live) as s:
                s.require_login()
                s.open("example.com", os=args.os, browser=args.browser)
                print(s.run(args.command))
            return 0
        if args.cmd == "shell":
            # Interactive by default shows the browser; --headless overrides.
            return run_shell(record=args.record, play=args.play, headless=args.headless)
        if args.cmd == "play":
            # Unattended replay: headless unless --live; exits when the file ends.
            return run_shell(play=args.file, headless=not args.live, exit_after_play=True)
    except BlingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
