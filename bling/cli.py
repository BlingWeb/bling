"""bling command line: login, har, open, run, shell, play.

bling login
bling har demo.browserling.com --out demo.har
bling urls demo.har
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

from . import HAR, Session, __version__, capture_here, har, login, ui
from .errors import BlingError
from .shell import run_shell


def _wait_until_closed(s) -> None:
    """Park on a --keep-open session with a live elapsed timer until the window closes."""
    start = time.monotonic()
    try:
        with ui.status("session open — close the window (or Ctrl-C here) to end") as st:
            while not s.page.is_closed():
                time.sleep(0.5)
                elapsed = int(time.monotonic() - start)
                st.update(f"session open · {elapsed}s — close the window to end")
    except KeyboardInterrupt:
        pass


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
    p.add_argument(
        "--proxy",
        choices=("datacenter", "residential", "mobile", "tor"),
        default=None,
        help="capture through a proxy/VPN of this kind (to see geo-cloaked content)",
    )
    p.add_argument("--country", default=None, help="proxy exit country, e.g. 'germany'")

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
        help="proxy exit country, matched case-insensitively, e.g. 'germany' or 'united states'",
    )

    p = sub.add_parser("urls", help="print every URL a .har file requested, one per line")
    p.add_argument("file", help="path to a .har file (from `bling har` or any browser)")
    p.add_argument("--summary", action="store_true", help="also show the HAR summary table")

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
            dest = args.out or _host(args.url) + ".har"
            if args.proxy:
                with Session(headless=not args.live) as s:
                    s.require_login()
                    label = args.proxy + (f" ({args.country})" if args.country else "")
                    with ui.status(f"opening a session, routing through {label} …"):
                        s.open("example.com", os=args.os, browser="firefox")
                        s.set_proxy(args.proxy, country=args.country)
                        s.wait_ready(timeout=45)
                    with ui.status(f"capturing {args.url} through the {args.proxy} proxy …"):
                        h = capture_here(s, args.url)
                h.save(dest)
            else:
                with ui.status(f"capturing {args.url} …"):
                    h = har(args.url, out=dest, os=args.os, live=args.live)
            ui.success(f"saved {dest}")
            ui.har_summary(h)
            return 0
        if args.cmd == "urls":
            h = HAR.load(args.file)
            for u in h.urls():
                print(u)  # data -> stdout, one per line, pipeable
            if args.summary:
                ui.har_summary(h)
            return 0
        if args.cmd == "open":
            live = args.live or args.keep_open  # keep-open is useless headless
            with Session(headless=not live) as s:
                s.require_login()
                with ui.status(f"opening {args.url} on {args.os}/{args.browser} …"):
                    state = s.open(args.url, os=args.os, browser=args.browser)
                if args.proxy:
                    label = f"{args.proxy} proxy" + (f" ({args.country})" if args.country else "")
                    with ui.status(f"routing through {label} …"):
                        s.set_proxy(args.proxy, country=args.country)
                        s.wait_ready(timeout=45)  # re-route restarts the VM stream
                ui.success(f"{state}: {args.url} on {args.os}/{args.browser}")
                if args.keep_open:
                    # Poll the outer page; closing the window flips is_closed() True. Robust
                    # in any context (TTY, pipe, background) — no stdin required.
                    _wait_until_closed(s)
                else:
                    print(s.screenshot(Path("_explore/session.png")))  # saved path -> stdout
            return 0
        if args.cmd == "run":
            with Session(headless=not args.live) as s:
                s.require_login()
                with ui.status("opening session …"):
                    s.open("example.com", os=args.os, browser=args.browser)
                with ui.status(f"running: {args.command} …"):
                    output = s.run(args.command)
                print(output)  # the command's output is data -> stdout, unstyled
            return 0
        if args.cmd == "shell":
            # Interactive by default shows the browser; --headless overrides.
            return run_shell(record=args.record, play=args.play, headless=args.headless)
        if args.cmd == "play":
            # Unattended replay: headless unless --live; exits when the file ends.
            return run_shell(play=args.file, headless=not args.live, exit_after_play=True)
    except BlingError as e:
        ui.error_panel(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
