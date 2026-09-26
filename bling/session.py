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

# Transfer hosts are a letter prefix plus a number: s8, s13, and b8 (seen 2026-09-17).
_TOKEN_RE = re.compile(r"https://([a-z]+\d+\.browserling\.com)/([A-Za-z0-9]+)/")


def _parse_curl_token(text: str) -> tuple[str, str] | None:
    """Extract (server, token) from a Browserling file-transfer 'see how' curl string.

    >>> _parse_curl_token("curl -O https://s8.browserling.com/abc123/file.txt")
    ('s8.browserling.com', 'abc123')
    """
    m = _TOKEN_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else None


# A bare filename in the VM's Downloads: no path, quotes, wildcards or shell metacharacters,
# because it is typed into a Win+R command line and put into an egress URL.
_VM_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _check_vm_name(name: str) -> None:
    """Raise BlingError unless ``name`` is a plain filename, e.g. ``launch_ab12.done``."""
    if not isinstance(name, str) or not _VM_NAME_RE.fullmatch(name):
        raise BlingError(
            f"{name!r} is not a plain filename (letters, digits, '.', '_', '-'; no path)"
        )


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

# Proxy kind -> the slug the panel uses in .use-<slug>, .screen-<slug>, #proxy-sel-<slug>.
_PROXY_SLUGS = {
    "datacenter": "dc",
    "residential": "res",
    "mobile": "mobile",
    "tor": "tor",
    "custom": "custom",
}
_PROXY_STEP_MS = 10_000  # one panel transition (open, card, disconnect)
_PROXY_CONNECT_MS = 45_000  # a connect; residential and Tor took up to ~10s live
_PROXY_REEXIT_MS = 8_000  # Set Location / New Identity swapped the exit ~3s after the click
_PROXY_SETTLE_MS = 1_000  # then the panel must hold still this long
# The status line's kind label starts with this word once each kind is up, e.g. "Tor Exit
# Node", "Residential IP". Custom was never seen live, so it is not checked.
_PROXY_STATUS = {"dc": "datacenter", "res": "residential", "mobile": "mobile", "tor": "tor"}
_PROXY_NO_PANEL = {
    "visible": False,
    "screen": None,
    "connected": None,
    "busy": False,
    "kind": "",
    "status": "",
    "buttons": [],
    "popup": "",
}

# Close every Browserling popup in #group-popups and return what each said. They sit over the
# control panel and intercept every click on it. Announcements ("Co-browsing is here!",
# 2026-09-16) carry a "Don't show again" box and a Close button: tick the box so this profile
# does not see it again. Errors ("Couldn't set proxy", 2026-09-25) carry only an OK button.
# Done in the page because the overlay is exactly what stops a locator click.
_TAKE_POPUPS_JS = r"""
() => [...document.querySelectorAll('#group-popups .popup[data-is-visible="true"]')].map(p => {
  const said = p.innerText.trim().replace(/\s*\n\s*/g, ' | ');
  const dont = p.querySelector('input[type=checkbox]');
  if (dont && !dont.checked) dont.click();
  const btn = [...p.querySelectorAll('button, .btn-close')].find(b =>
    /close/i.test(b.textContent || '') || /close/i.test(b.className)
    || /^ok$/i.test((b.textContent || '').trim()));
  if (btn) btn.click();
  return said;
})
"""


class _ProxyRefused(Exception):
    """Browserling put up an error popup (e.g. "Couldn't set proxy") during set_proxy."""


# The proxy panel's state, for branching in set_proxy and for its error message. The panel
# hides screens with visibility:hidden, which offsetParent does not see, so buttons are
# tested with checkVisibility. "busy" means a visible button is disabled ("Connecting...").
_PROXY_STATE_JS = r"""
() => {
  const w = document.querySelector('.proxy-settings');
  if (!w) return null;
  const shown = [...w.querySelectorAll('button')]
    .filter(b => b.checkVisibility({visibilityProperty: true}));
  const text = (css) => { const e = w.querySelector(css); return e ? e.innerText.trim() : ''; };
  return {
    visible: w.dataset.isVisible === 'true',
    screen: w.dataset.screen || null,
    connected: w.dataset.connected || null,
    busy: shown.some(b => b.disabled),
    kind: text('.proxy-kind'),
    status: text('.proxy-status').replace(/\s*\n\s*/g, ' | '),
    buttons: shown.map(b => b.innerText.trim() + (b.disabled ? ' (disabled)' : '')),
    popup: [...document.querySelectorAll('#group-popups .popup[data-is-visible="true"]')]
      .map(p => p.innerText.trim().replace(/\s*\n\s*/g, ' | ')).join(' / '),
  };
}
"""

# Find a country in one kind's select; on a miss, list what it offers and which kinds have it.
_PROXY_FIND_JS = r"""
([slug, country]) => {
  const want = country.toLowerCase();
  const labels = (sel) => [...sel.options].map(o => o.textContent.trim());
  const hit = (sel) => [...sel.options].find(o => o.textContent.toLowerCase().includes(want));
  const sel = document.querySelector('#proxy-sel-' + slug);
  const m = hit(sel);
  const elsewhere = [...document.querySelectorAll('select[id^="proxy-sel-"]')]
    .filter(s => s !== sel && hit(s)).map(s => s.id.replace('proxy-sel-', ''));
  return { value: m ? m.value : null, offered: labels(sel), elsewhere };
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

        Returns once the panel's status line shows the new exit and it has held for a second.
        Raises BlingError, naming the step that stalled and carrying the panel's state and any
        Playwright call log, if it does not. A country this kind does not offer is refused
        before anything changes, so the proxy the session already has is left alone.

        How the panel behaves (inspected live 2026-09-25): the window carries
        ``data-screen="none|dc|res|mobile|tor|custom"`` and, while a proxy is up,
        ``data-connected``. With nothing connected it opens on a chooser of kind cards. With a
        proxy connected it reopens straight on *that* kind's screen, with the chooser and every
        other kind's country select hidden, so asking for a different kind used to wait 30s on
        an invisible select. We now press Disconnect first to get back to the chooser.

        The apply button is ``.do-connect`` whatever its label: "Connect" when idle, "Set
        Location" once connected, and "New Identity" on an active Tor screen. "Connect" shows
        "Connecting..." with both buttons disabled; the other two leave the buttons enabled and
        swap the exit about 3s later. A Disconnect sent inside that window was undone when the
        new exit arrived, so each change waits for the status line rather than the buttons.
        """
        slug = _PROXY_SLUGS.get(kind)
        if slug is None:
            raise BlingError(f"unknown proxy kind {kind!r} (one of {', '.join(_PROXY_SLUGS)})")
        step = "opening the proxy panel"
        try:
            self._take_popups()  # one left over from earlier blocks every click below
            self._open_proxy_panel()
            # Check the country first: its select is in the page even while hidden, and a
            # miss must not disconnect the proxy the session already has.
            value = (
                self._proxy_country_value(slug, country) if country and slug != "custom" else None
            )
            step = "waiting out a connect already in progress"
            self._await_proxy(lambda p: not p["busy"])
            here = self._proxy_state()["screen"]
            if here not in ("none", slug):
                step = f"leaving the {here} screen"
                self._leave_proxy_screen(here)
            if self._proxy_state()["screen"] == "none":
                step = f"opening the {kind} screen"
                self._click_visible(f".proxy-settings .use-btn.use-{slug}")
                self._await_proxy(lambda p: p["screen"] == slug)
            screen = f".proxy-settings .screen-{slug}"
            step = "filling in the form"
            if kind == "custom":
                self._ready(f"{screen} #proxy-custom-protocol").select_option(label=protocol)
                for css, val in (
                    ("#proxy-custom-address", address),
                    ("#proxy-custom-username", username),
                    ("#proxy-custom-password", password),
                ):
                    if val:
                        self._ready(f"{screen} {css}").fill(val)
            elif value is not None:
                self._ready(f"#proxy-sel-{slug}").select_option(value=value)
            step = "connecting"

            def up(p: dict) -> bool:
                return (
                    p["screen"] == slug
                    and bool(p["connected"])
                    and not p["busy"]
                    and (slug == "custom" or p["kind"].lower().startswith(_PROXY_STATUS[slug]))
                )

            for attempt in (1, 2):
                before = self._proxy_state()["status"]
                self._click_visible(f"{screen} .do-connect")
                try:
                    self._await_proxy(up, _PROXY_CONNECT_MS)
                    # Set Location and New Identity swap the exit late. The exit can also
                    # come back the same, so an unchanged status line is not an error.
                    self._await_proxy(
                        lambda p, b=before: p["status"] != b, _PROXY_REEXIT_MS, required=False
                    )
                    break
                except _ProxyRefused:
                    # Browserling's own words are "please try again"; one retry rides out a
                    # blip, and a second refusal is reported.
                    if attempt == 2:
                        raise
                    self._await_proxy(lambda p: not p["busy"])
            step = "checking the new exit held"
            self.page.wait_for_timeout(_PROXY_SETTLE_MS)
            self._await_proxy(up, 0)
        except _ProxyRefused as e:
            raise BlingError(
                f"Browserling refused set_proxy({kind!r}, country={country!r}) while {step}: "
                f"{e}\nThe session keeps: {self._proxy_state()['status']}"
            ) from None
        except PWTimeout as e:
            raise BlingError(
                f"set_proxy({kind!r}, country={country!r}) stalled while {step}.\n"
                f"panel: {self._proxy_state()}\n{e}"
            ) from e

    def _leave_proxy_screen(self, here: str) -> None:
        """Back out of kind ``here``'s screen to the chooser: Disconnect if that kind is live,
        otherwise its plain Cancel. Tries twice if the panel snaps back."""
        for _ in range(2):
            live = bool(self._proxy_state()["connected"])
            row = ".buttons-active" if live else ".buttons-default"
            self._click_visible(f".proxy-settings .screen-{here} {row} .do-cancel")
            self._await_proxy(lambda p: p["screen"] == "none" and not p["connected"])
            self.page.wait_for_timeout(_PROXY_SETTLE_MS)
            if self._proxy_state()["screen"] == "none":
                return
            here = self._proxy_state()["screen"] or here
        raise PWTimeout(f"the proxy panel went back to the {here} screen after Disconnect")

    def _open_proxy_panel(self) -> None:
        if not self._proxy_state()["visible"]:
            self._close_popups()
            self._open_menu("proxy")
        self._await_proxy(lambda p: p["visible"])

    def _proxy_state(self) -> dict:
        """The proxy panel as data: visible, screen, connected, busy, the status line's kind
        and full text, and the visible buttons."""
        try:
            return self.page.evaluate(_PROXY_STATE_JS) or dict(_PROXY_NO_PANEL)
        except Exception as e:  # noqa: BLE001 - only used for diagnosis and branching
            return dict(_PROXY_NO_PANEL, error=str(e))

    def _await_proxy(self, cond, timeout: int = _PROXY_STEP_MS, *, required: bool = True):
        """Poll the panel until ``cond(state)`` holds. On timeout raise Playwright's
        TimeoutError (set_proxy turns it into a BlingError), unless ``required`` is False."""
        end = time.time() + timeout / 1000
        while True:
            state = self._proxy_state()
            if state["popup"]:
                raise _ProxyRefused(" / ".join(self._take_popups()) or state["popup"])
            if cond(state):
                return
            if time.time() >= end:
                if required:
                    raise PWTimeout(f"Timeout {timeout}ms exceeded waiting on the proxy panel")
                return
            self.page.wait_for_timeout(250)

    def _ready(self, css: str):
        """The first match of ``css``, once it is visible and enabled."""
        loc = self.page.locator(css).first
        loc.wait_for(state="visible", timeout=_PROXY_STEP_MS)
        self.page.wait_for_function(
            "el => !el.disabled", arg=loc.element_handle(), timeout=_PROXY_STEP_MS
        )
        return loc

    def _click_visible(self, css: str) -> None:
        """Click the visible match of ``css``. Each proxy screen repeats its button classes in
        a hidden row, so plain ``.first`` can land on the hidden one."""
        self._ready(f"{css} >> visible=true").click(timeout=_PROXY_STEP_MS)

    def _proxy_country_value(self, slug: str, country: str) -> str:
        """The option value for a proxy location, found by name. The options are labelled with
        a flag emoji (e.g. ``"🇩🇪 Germany"``), so match a case-insensitive substring.

        Each kind has its own list: residential has no Canada, while mobile, datacenter and
        Tor do. So a miss names what this kind offers and which other kinds have the country.
        """
        found = self.page.evaluate(_PROXY_FIND_JS, [slug, country])
        if found["value"] is None:
            kind = next(k for k, v in _PROXY_SLUGS.items() if v == slug)
            others = [k for k, v in _PROXY_SLUGS.items() if v in found["elsewhere"]]
            msg = f"no {kind} proxy location matches {country!r}; {kind} offers: "
            msg += ", ".join(found["offered"])
            if others:
                msg += f". {country!r} is offered by: {', '.join(others)}"
            raise BlingError(msg)
        return found["value"]

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

        Browserling can hand back a VM an earlier session used, Downloads and all. So if
        ``sentinel`` is already there, it is deleted before launch (and BlingError raised if
        it will not go); otherwise this would return at once on the old file. The script's
        other outputs are the caller's to clear: ``remove()`` them first, or name them
        uniquely, or a later ``download()`` can fetch the previous session's copy.
        """
        _check_vm_name(sentinel)
        server, token = (
            self.transfer_token()
        )  # cache before Win+R; opening it mid-keystroke would steal focus
        if self._vm_file_exists(sentinel):
            self.remove(sentinel)
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

    def remove(self, *remote_names: str) -> None:
        """Delete files from the VM's Downloads, and confirm through the egress that they
        are gone. Use it to clear a script's outputs before re-running it on a VM that may
        be reused. Raises BlingError if a file is still there afterwards (locked, say).

        >>> s.remove("result.txt", "done.flag")
        """
        if not remote_names:
            return
        for name in remote_names:
            _check_vm_name(name)
        dl = r"%USERPROFILE%\Downloads"
        self.run("del /q " + " ".join(rf"{dl}\{n}" for n in remote_names))
        left = [n for n in remote_names if self._vm_file_exists(n)]
        if left:
            raise BlingError(f"could not delete {', '.join(left)} from the VM's Downloads")

    def _vm_file_exists(self, remote_name: str) -> bool:
        """True if the egress serves ``remote_name`` from the VM's Downloads right now."""
        server, token = self.transfer_token()
        try:
            r = requests.get(f"https://{server}/{token}/{remote_name}", timeout=20)
        except requests.RequestException as e:
            raise EgressError(f"could not check {remote_name!r} in the VM: {e}") from e
        return r.status_code == 200

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
        # Every other control-panel dialog is an .interactive-window carrying its own
        # close control. Display Settings is one, and it used to stay open over the page
        # after set_resolution, which put a Browserling dialog into screenshots meant to
        # show the site. Escape alone does not close it.
        for i in range(self.page.locator(".interactive-window").count()):
            panel = self.page.locator(".interactive-window").nth(i)
            try:
                if not panel.is_visible():
                    continue
                shut = panel.locator(".close").first
                if shut.count() and shut.is_visible():
                    shut.click(timeout=1500)
                    self.page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001 - a dialog that will not close is not fatal
                pass
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
        # Browserling's popups block End session, the go button and the proxy panel until
        # closed; see _TAKE_POPUPS_JS.
        self._take_popups()
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

    def _take_popups(self) -> list[str]:
        """Close any Browserling popup over the control panel; return what each one said."""
        try:
            said = self.page.evaluate(_TAKE_POPUPS_JS)
        except Exception:  # noqa: BLE001 - a page mid-navigation has no popups to close
            return []
        if said:
            self.page.wait_for_timeout(200)
        return said

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
