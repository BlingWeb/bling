"""A Browserling session you can drive.

A session has two layers, and the API hides the seam:
  * OUTER page (browserling.com) — real DOM, driven with Playwright (the control panel).
  * INNER remote browser — pixels in a canvas; synthetic keyboard/mouse forward into the VM,
    so we drive it "blind" (Win+R, keystrokes). The VM is a full Windows box with admin.

Auth is a one-time human step (reCAPTCHA, never auto-solved): run ``bling login`` once; the
cookie persists in the profile so later runs are unattended until it expires.
"""

from __future__ import annotations

import atexit
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from dotenv import find_dotenv, load_dotenv
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import config
from .errors import BlingError, EgressError, NotLoggedIn, NotReady, SessionBlocked, Timeout

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page, Playwright

_TOKEN_RE = re.compile(r"https://(s\d+\.browserling\.com)/([A-Za-z0-9]+)/")


def _parse_curl_token(text: str) -> tuple[str, str] | None:
    """Extract (server, token) from a Browserling file-transfer 'see how' curl string.

    >>> _parse_curl_token("curl -O https://s8.browserling.com/abc123/file.txt")
    ('s8.browserling.com', 'abc123')
    """
    m = _TOKEN_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else None


# Which block screens are visible, and is the canvas up? Run once per readiness poll.
_STATE_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 1 && r.height > 1; };
  const active = [];
  document.querySelectorAll('.block-screen, .queue-screen').forEach(b => {
    if (vis(b)) active.push((b.id ? b.id + ' ' : '') + (b.className || '')); });
  const bs = document.querySelector('#browser-screen');
  let canvasW = 0;
  if (bs) {
    const c = bs.querySelector('canvas');
    if (c) canvasW = Math.round(c.getBoundingClientRect().width);
  }
  return { active, canvasW };
}
"""


class Session:
    """One Browserling session. Use it either way — as a context manager, or step by step.

    Context manager (preferred for scripts — cleanup is automatic):

    >>> with bling.Session() as s:
    ...     s.require_login()
    ...     s.open("example.com")
    ...     print(s.run("whoami"))
    ...     s.download("example.com.har", "out.har")

    Step by step in a REPL (plain ``python`` or terminal IPython — drive it line by line):

    >>> s = bling.Session(headless=False)
    >>> s.start()               # brings the browser up (or use `with`, not both)
    >>> s.require_login()
    >>> s.open("example.com")
    'ready'
    >>> s.screenshot("shot.png")
    >>> s.close()               # ends everything; also runs automatically at exit

    ``start()`` registers an ``atexit`` hook, so an interpreter you simply abandon still
    releases the remote VM instead of stranding a live session ("too many sessions").
    ``close()`` is idempotent, and ``with`` still closes exactly once.

    Note: the synchronous Playwright backend refuses to run inside an already-running
    asyncio loop, so the step-by-step path works in plain ``python`` and terminal IPython
    but **raises inside a Jupyter notebook**. Use the context manager from scripts there.
    """

    # Set in start() (until then, the session has no live browser).
    page: Page
    _pw: Playwright | None
    _ctx: BrowserContext | None

    def __init__(self, profile: str | None = None, *, headless: bool = True):
        self.profile = profile or config.PROFILE
        self.headless = headless
        self._dl_token: tuple[str, str] | None = None
        self._ul_token: tuple[str, str] | None = None
        self._pw = None
        self._ctx = None

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> Session:
        """Bring the browser up and grab the page. Returns self, so ``s = Session().start()``.

        Idempotent-ish: calling it on an already-started session just returns self. Prefer
        the context manager in scripts; use this to drive a Session by hand in a REPL.

        >>> s = bling.Session(headless=False).start()
        >>> s.open("example.com")
        'ready'
        """
        if self._ctx is not None:
            return self  # already started — don't launch a second browser
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.profile,
            channel="chrome",
            headless=self.headless,
            viewport=config.VIEWPORT,
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        # An abandoned REPL must not strand a live VM; this guarantees one final close().
        atexit.register(self.close)
        return self

    def close(self) -> None:
        """Close the browser and stop Playwright. Idempotent — safe to call more than once.

        Called for you by the context manager's exit and by the ``atexit`` hook, so a
        forgotten REPL session still cleans up. Explicit calls are fine too.
        """
        ctx, pw = self._ctx, self._pw
        # Clear first so a re-entrant or double call (with-exit + atexit) is a no-op.
        self._ctx = self._pw = None
        atexit.unregister(self.close)
        try:
            if ctx:
                ctx.close()
        finally:
            if pw:
                pw.stop()

    def __enter__(self) -> Session:
        return self.start()

    def __exit__(self, *exc) -> None:
        # Always close so a frozen tab can't leave a live VM ("too many sessions").
        self.close()

    # --- auth ---------------------------------------------------------------
    def is_logged_in(self) -> bool:
        """True if the persistent cookie still authenticates us."""
        self.page.goto(config.HOME, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(1000)
        self._dismiss_promo()
        sign_in = self.page.locator("#sign-in")
        return sign_in.count() == 0 or not sign_in.is_visible()

    def require_login(self) -> None:
        """Raise NotLoggedIn (with the fix) if the cookie has expired."""
        if not self.is_logged_in():
            raise NotLoggedIn("Not logged in — run once:  bling login")

    # --- open / lifecycle ---------------------------------------------------
    def open(
        self, target: str, *, os: str = "win10", browser: str = "chrome138", ready_timeout: int = 45
    ) -> str:
        """Open a session at ``target`` and wait until the remote canvas is up.

        >>> s.open("example.com", os="win10", browser="firefox")
        'ready'
        """
        self._dl_token = self._ul_token = None  # new session -> new tokens
        url = config.BROWSE.format(os=os, browser=browser, target=target)
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._dismiss_promo()
        return self.wait_ready(ready_timeout)

    def wait_ready(self, timeout: int = 45) -> str:
        """Poll until the canvas paints; raise SessionBlocked on a fatal screen."""
        end = time.time() + timeout
        while time.time() < end:
            st = self.page.evaluate(_STATE_JS)
            active = st["active"]
            # Only fail on a fatal screen once nothing transient is up (avoid racing a
            # mid-transition screen that briefly shows alongside the spinner).
            if not any(t in e for e in active for t in config.TRANSIENT):
                for entry in active:
                    for key, why in config.FATAL.items():
                        if key in entry:
                            raise SessionBlocked(f"session blocked: {why} [{entry.strip()}]")
            if st["canvasW"] > 100:
                self.page.wait_for_timeout(1200)  # let it paint
                return "ready"
            self.page.wait_for_timeout(1000)
        raise NotReady("session did not become ready in time")

    def end(self) -> None:
        """End the session via the control panel (the context manager also cleans up)."""
        self._close_popups()
        self._open_menu("end")
        self.page.wait_for_timeout(500)

    # --- control panel (outer DOM) -----------------------------------------
    def navigate(self, url: str, *, via: str = "panel") -> None:
        """Load a new URL in the running session.

        via="panel"  -> control-panel URL field (resets the view).
        via="remote" -> the remote browser's own address bar (keeps its DevTools open).
        """
        if via == "remote":
            self.canvas_click(*config.REMOTE_ADDR_BAR)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.type(url, delay=20)
            self.page.keyboard.press("Enter")
        else:
            self.page.locator("input.input.text-input").first.fill(url)
            self.page.locator("button.button-go").first.click()
        self.page.wait_for_timeout(800)

    def set_resolution(self, value: str) -> None:
        """Set the remote screen resolution, e.g. ``"1920x1080"``."""
        self._open_menu("display")
        norm = value.lower().replace("x", "×")
        self.page.locator(".resolution", has_text=norm).first.click(timeout=4000)
        self.page.wait_for_timeout(400)

    def set_proxy(
        self,
        kind: str = "datacenter",
        *,
        country: str | None = None,
        address: str | None = None,
        username: str | None = None,
        password: str | None = None,
        protocol: str = "SOCKS5",
    ) -> None:
        """Route the session through a proxy/VPN.

        kind: ``"datacenter" | "residential" | "mobile" | "tor" | "custom"``.
        """
        self._open_menu("proxy")
        # The panel opens on a chooser of type cards; each kind's screen (and its country
        # dropdown) stays hidden until you click that card. So click it first.
        card = {
            "datacenter": "use-dc",
            "residential": "use-res",
            "mobile": "use-mobile",
            "tor": "use-tor",
            "custom": "use-custom",
        }.get(kind)
        if card is None:
            raise BlingError(f"unknown proxy kind {kind!r}")
        # The panel usually opens on a chooser of type cards — click ours to reveal its screen.
        # But if the account has a remembered proxy it can open on that screen already, with the
        # chooser card hidden; then there's nothing to click, so only click when it's visible.
        card_btn = self.page.locator(f".use-btn.{card}").first
        try:
            if card_btn.is_visible():
                card_btn.click(timeout=4000)
                self.page.wait_for_timeout(400)
        except PWTimeout as e:
            raise BlingError(f"couldn't open the {kind} proxy screen (.use-btn.{card})") from e
        screen = {
            "datacenter": ".screen-dc",
            "residential": ".screen-res",
            "mobile": ".screen-mobile",
            "tor": ".screen-tor",
            "custom": ".screen-custom",
        }[kind]
        sel = {
            "datacenter": "#proxy-sel-dc",
            "residential": "#proxy-sel-res",
            "mobile": "#proxy-sel-mobile",
            "tor": "#proxy-sel-tor",
        }.get(kind)
        if kind == "custom":
            self.page.locator("#proxy-custom-protocol").select_option(label=protocol)
            for css, val in (
                ("#proxy-custom-address", address),
                ("#proxy-custom-username", username),
                ("#proxy-custom-password", password),
            ):
                if val:
                    self.page.locator(css).fill(val)
        elif country and sel:
            self._select_proxy_country(sel, country)
        self.page.locator(f"{screen} .do-connect").first.click(timeout=4000)
        self.page.wait_for_timeout(800)

    def _select_proxy_country(self, sel: str, country: str) -> None:
        """Pick a proxy location by name. The options are labelled with a flag emoji (e.g.
        ``"🇩🇪 Germany"``), so match a case-insensitive substring rather than the exact label.
        """
        loc = self.page.locator(sel)
        value = loc.evaluate(
            """(el, c) => {
                const want = c.toLowerCase();
                const m = [...el.options].find(o => o.textContent.toLowerCase().includes(want));
                return m ? m.value : null;
            }""",
            country,
        )
        if value is None:
            raise BlingError(f"no proxy location matches {country!r} — check the spelling")
        loc.select_option(value=value)

    # --- file transfer (curl egress) ---------------------------------------
    def upload(self, local_path, remote_name: str | None = None) -> str:
        """Push a local file INTO the VM (lands in the VM's Downloads). Returns its name."""
        local_path = Path(local_path)
        return self.upload_bytes(local_path.read_bytes(), remote_name or local_path.name)

    def upload_text(self, text: str, remote_name: str) -> str:
        """Write a small text file (a script, a user.js, ...) into the VM's Downloads."""
        return self.upload_bytes(text.encode("utf-8"), remote_name)

    def upload_bytes(self, data: bytes, remote_name: str) -> str:
        server, token = self.upload_token()
        try:
            r = requests.put(f"https://{server}/{token}/{remote_name}", data=data, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            raise EgressError(f"upload of {remote_name!r} failed: {e}") from e
        return remote_name

    def download(self, remote_name: str, out) -> Path:
        """Pull a file out of the VM's Downloads to the local machine. Returns the path."""
        out = Path(out)
        server, token = self.transfer_token()
        try:
            r = requests.get(f"https://{server}/{token}/{remote_name}", timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            raise EgressError(f"download of {remote_name!r} failed: {e}") from e
        out.write_bytes(r.content)
        return out

    def download_when_ready(
        self, remote_name: str, out, *, timeout: int = 45, poll: float = 3.0
    ) -> Path:
        """Download a VM file once it exists and its size has settled (finished being
        written). Use for files the VM writes asynchronously — e.g. an auto-exported HAR.
        Polls the egress; raises Timeout if it never appears/settles.
        """
        out = Path(out)
        server, token = self.transfer_token()
        end = time.time() + timeout
        last = -1
        stable = 0
        while time.time() < end:
            try:
                r = requests.get(f"https://{server}/{token}/{remote_name}", timeout=20)
                if r.status_code == 200 and r.content:
                    n = len(r.content)
                    if n == last:
                        stable += 1
                        if stable >= 2:  # size unchanged across polls -> write complete
                            out.write_bytes(r.content)
                            return out
                    else:
                        stable, last = 0, n
                else:
                    stable, last = 0, -1
            except requests.RequestException:
                pass
            self.page.wait_for_timeout(int(poll * 1000))
        raise Timeout(f"{remote_name!r} did not appear/settle within {timeout}s")

    def transfer_token(self) -> tuple[str, str]:
        """Per-session egress (server, token) for downloads. Cached."""
        if self._dl_token is None:
            self._dl_token = self._read_curl_token("is-download", "howto-curl-download")
        return self._dl_token

    def upload_token(self) -> tuple[str, str]:
        """Per-session ingress (server, token) for uploads (HTTP PUT). Cached."""
        if self._ul_token is None:
            self._ul_token = self._read_curl_token("is-upload", "howto-curl-upload")
        return self._ul_token

    # --- VM control (inner, blind) -----------------------------------------
    def run(self, command: str, *, timeout: int = 60, poll: float = 2.0) -> str:
        """Run a shell command in the VM and return its combined stdout+stderr.

        The console is blind pixels, so output is redirected to a log + DONE marker and
        polled out via the curl egress. Keep ``command`` short (Win+R caps ~255 chars, no
        embedded double-quotes); for more, upload a script and run it by path.

        >>> s.run("whoami")
        'win10\\\\user'
        """
        if '"' in command:
            raise BlingError(
                "command must not contain double-quotes for Win+R routing — "
                "upload a script and run it by path instead"
            )
        tag = uuid.uuid4().hex[:8]
        log = f"_blrun_{tag}.log"
        marker = f"__DONE_{tag}__"
        dl = rf"%USERPROFILE%\Downloads\{log}"
        launch = f'cmd /c "({command}) > {dl} 2>&1 & echo {marker}>> {dl}"'
        if len(launch) > 255:
            raise BlingError("command too long for Win+R — upload a script and run it by path")
        server, token = self.transfer_token()  # cache before launch; poll is HTTP-only
        self.focus_vm()
        self.page.keyboard.press("Meta+r")  # Win+R
        self.page.wait_for_timeout(900)
        self.page.keyboard.type(launch, delay=8)
        self.page.keyboard.press("Enter")
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            self.page.wait_for_timeout(int(poll * 1000))
            try:
                r = requests.get(f"https://{server}/{token}/{log}", timeout=20)
                if r.status_code == 200:
                    last = r.text
                    if marker in last:
                        return last.split(marker)[0].rstrip()
            except requests.RequestException:
                pass
        raise Timeout(f"run() timed out after {timeout}s; partial output:\n{last}")

    def run_script(
        self, remote_name: str, *, sentinel: str, timeout: int = 90, poll: float = 3.0
    ) -> None:
        """Launch an uploaded .bat/.py/.ps1 in the VM and wait until it writes ``sentinel``
        (a filename it creates in Downloads).

        Use this for scripts that spawn apps or run long; ``run()`` redirects output to a
        log, and a process the script ``start``s would inherit (and lock) that handle. For
        a quick command whose output you want, use ``run()`` instead.
        """
        server, token = (
            self.transfer_token()
        )  # cache before Win+R; opening it mid-keystroke would steal focus
        self.focus_vm()
        self.page.keyboard.press("Meta+r")
        self.page.wait_for_timeout(900)
        self.page.keyboard.type(rf"cmd /c %USERPROFILE%\Downloads\{remote_name}", delay=10)
        self.page.keyboard.press("Enter")
        end = time.time() + timeout
        n = 0
        while time.time() < end:
            self.page.wait_for_timeout(int(poll * 1000))
            try:
                r = requests.get(f"https://{server}/{token}/{sentinel}", timeout=20)
                if r.status_code == 200 and r.content.strip():
                    return
            except requests.RequestException:
                pass
            n += 1
            if n % 20 == 0:  # keepalive so the VM doesn't idle-timeout on long waits
                self.focus_vm()
                self.page.keyboard.press("Shift")
        raise Timeout(f"{remote_name} did not finish (no {sentinel}) within {timeout}s")

    def focus_vm(self, point: tuple[int, int] = config.REMOTE_FOCUS) -> None:
        """Give the VM keyboard focus so OS shortcuts (Win+R) forward. No side effects."""
        self.page.mouse.click(*point)
        self.page.wait_for_timeout(250)

    def key(self, combo: str) -> None:
        """Press a key/chord in the focused VM window, e.g. ``"Control+Shift+E"``."""
        self.page.keyboard.press(combo)

    def type(self, text: str, *, delay: int = 20) -> None:
        """Type into the focused VM window."""
        self.page.keyboard.type(text, delay=delay)

    def canvas_click(self, x: int, y: int, *, clicks: int = 1) -> None:
        """Click a pixel in the remote view."""
        self.page.mouse.click(x, y, click_count=clicks)
        self.page.wait_for_timeout(150)

    def screenshot(self, path) -> Path:
        """Save a PNG of the session (the streamed remote view + control panel)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path))
        return path

    def dismiss(self) -> None:
        """Close any open control-panel dialog/popup and return keyboard focus to the VM.
        Call this after file-transfer ops before sending VM keystrokes."""
        win = self.page.locator(".file-manager.interactive-window")
        if win.count() and win.first.is_visible():
            self.page.locator(".button-item.file-transfer").first.click()
            self.page.wait_for_timeout(400)
        self._close_popups()
        self.focus_vm()
        self.focus_vm()

    # --- internals ----------------------------------------------------------
    def _read_curl_token(self, tab_cls: str, link_cls: str) -> tuple[str, str]:
        self._ensure_files_open()
        self.page.locator(f".tab.{tab_cls}").first.click()
        self.page.wait_for_timeout(300)
        self.page.locator(f"a.{link_cls}").first.click()
        self.page.wait_for_timeout(600)
        cmd = self.page.evaluate(r"""() => {
          const pop = document.querySelector('.fm-curl-popup');
          if (!pop) return '';
          let s = pop.innerText || '';
          pop.querySelectorAll('input,textarea').forEach(i => { s += ' ' + (i.value || ''); });
          return s;
        }""")
        self._close_popups()
        parsed = _parse_curl_token(cmd)
        if parsed is None:
            raise EgressError(f"could not read transfer token from '{link_cls}' popup")
        return parsed

    def _ensure_files_open(self) -> None:
        win = self.page.locator(".file-manager.interactive-window")
        if not (win.count() and win.first.is_visible()):
            self._open_menu("files")
        self.page.wait_for_timeout(200)

    def _open_menu(self, item: str) -> None:
        slug = config.MENU_ITEMS[item]
        try:
            self.page.locator(f".button-item.{slug}").first.click(timeout=4000)
        except PWTimeout as e:
            raise BlingError(f"menu item '{item}' (.button-item.{slug}) not clickable") from e
        self.page.wait_for_timeout(400)

    def _close_popups(self) -> None:
        try:
            btn = self.page.locator(".fm-curl-popup").get_by_role("button", name="Close")
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=1500)
                self.page.wait_for_timeout(200)
                return
        except Exception:
            pass
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
        except Exception:
            pass

    def _dismiss_promo(self) -> None:
        try:
            if self.page.locator("#bfClose").is_visible():
                self.page.locator("#bfClose").click(timeout=2000)
                self.page.wait_for_timeout(300)
        except Exception:
            pass


def login(profile: str | None = None, *, wait: int = 240) -> None:
    """One-time human login (you solve the reCAPTCHA — bling never auto-solves it).

    Opens a headed Chrome on the persistent profile, pre-fills BROWSERLING_EMAIL /
    BROWSERLING_PASSWORD if present, and waits for you to finish. The cookie then persists.
    """
    import os

    load_dotenv(find_dotenv("keys.env", usecwd=True))  # credentials are only needed here
    load_dotenv()
    profile = profile or config.PROFILE
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile, channel="chrome", headless=False, viewport=config.VIEWPORT
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.HOME, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1000)
        try:
            if page.locator("#bfClose").is_visible():
                page.locator("#bfClose").click(timeout=2000)
        except Exception:
            pass
        sign_in = page.locator("#sign-in")
        if sign_in.count() == 0 or not sign_in.is_visible():
            print("already logged in")
            ctx.close()
            return
        try:
            sign_in.click(timeout=3000)
        except Exception:
            page.eval_on_selector("#sign-in", "el => el.click()")
        page.wait_for_timeout(900)
        email, pw = os.getenv("BROWSERLING_EMAIL"), os.getenv("BROWSERLING_PASSWORD")
        if email and pw:
            try:
                page.fill("#br-login-email", email)
                page.fill("#br-login-password", pw)
                print("pre-filled credentials")
            except Exception as e:
                print("pre-fill skipped:", e)
        print(f">>> Solve the CAPTCHA and click Continue. Waiting up to {wait}s...")
        end = time.time() + wait
        while time.time() < end:
            if not page.locator("#sign-in").is_visible():
                print("login complete; cookie saved to the profile")
                ctx.close()
                return
            page.wait_for_timeout(1500)
        ctx.close()
        raise Timeout("login not completed in time — re-run: bling login")
