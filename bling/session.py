"""A Browserling session you can drive.

A session has two layers, and the API hides the seam:
  * OUTER page (browserling.com) — real DOM, driven with Playwright (the control panel).
  * INNER remote browser — pixels in a canvas; synthetic keyboard/mouse forward into the VM,
    so we drive it "blind" (Win+R, keystrokes). The VM is a full Windows box with admin,
    or a macOS box driven through Terminal when opened with os="mac".

Auth is a one-time human step (reCAPTCHA, never auto-solved): run ``bling login`` once; the
cookie persists in the profile so later runs are unattended until it expires.
"""

from __future__ import annotations

import atexit
import re
import time
import uuid
from contextlib import contextmanager
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

# The session menu's own lists, read from the page (they are in the DOM even while closed):
# OS names, each browser with the versions it lists, the Apps group, and iOS device models.
_MENU_LISTS_JS = r"""
() => {
  const txt = (e) => (e ? e.textContent.trim() : '');
  const menu = document.querySelector('#main-menu');
  const heads = [...menu.querySelectorAll('.field.browser .option-browser')]
    .filter(h => !h.closest('.option-apps'));
  const browsers = {};
  for (const h of heads) {
    const group = h.nextElementSibling;
    browsers[txt(h.querySelector('.select-name'))] =
      group ? [...group.querySelectorAll('.option-version')].map(txt) : [];
  }
  return {
    platforms: [...menu.querySelectorAll('.field.platform .option-item .select-name')].map(txt),
    browsers,
    apps: [...menu.querySelectorAll('.field.browser .option-apps .option-version')].map(txt),
    devices: [...menu.querySelectorAll('.field.model .option-iosmodel')].map(txt),
    browser: txt(menu.querySelector('.field.browser .select-dropdown .select-name')),
    platform: txt(menu.querySelector('.field.platform .select-dropdown .select-name')),
    device: txt(menu.querySelector('.field.model .select-dropdown .select-name')),
  };
}
"""

# Mark the version chip to click for one browser. Each browser lists its newest 15 versions
# and an "... N more" chip that adds 30 per click, so an older version is reached by
# expanding. The mark lets Playwright do the click (and the scrolling) itself.
_BROWSER_PICK_JS = r"""
([name, version]) => {
  document.querySelectorAll('[data-bling-pick]').forEach(e => e.removeAttribute('data-bling-pick'));
  const txt = (e) => (e ? e.textContent.trim() : '');
  const heads = [...document.querySelectorAll('#main-menu .field.browser .option-browser')]
    .filter(h => !h.closest('.option-apps'));
  const label = (h) => txt(h.querySelector('.select-name'));
  const head = heads.find(h => label(h).toLowerCase() === name.toLowerCase());
  if (!head) return { state: 'no-browser', offered: heads.map(label) };
  const group = head.nextElementSibling;
  const chips = [...group.querySelectorAll('.option-version')];
  const chip = version == null ? chips[0] : chips.find(c => txt(c) === String(version));
  if (chip) {
    chip.setAttribute('data-bling-pick', '');
    return { state: 'found', name: label(head), version: txt(chip) };
  }
  const more = group.querySelector('.option-expand');
  if (more) { more.setAttribute('data-bling-pick', ''); return { state: 'expand' }; }
  return { state: 'no-version', offered: chips.map(txt) };
}
"""
_BROWSER_EXPANDS = 12  # 15 + 12 x 30 versions: more than any browser lists


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
        self.os = "win10"  # set by open(); run() and friends pick Win+R or Terminal by it
        self._target: str | None = None  # the address the session is on; set_os() reopens it
        self._pw = None
        self._ctx = None

    @property
    def platform(self) -> str:
        """The open session's OS family: ``"win"``, ``"mac"``, ``"ios"`` or ``"android"``.

        >>> s.open("example.com", os="iOS 18", browser="safari")
        'ready'
        >>> s.platform
        'ios'
        """
        return config.os_family(self.os)

    @property
    def is_mac(self) -> bool:
        """True when the open session is a macOS VM (commands go through Terminal).

        >>> s.is_mac
        False
        """
        return self.platform == "mac"

    @contextmanager
    def _step(self, what: str):
        """Turn a Playwright timeout inside the block into a BlingError that names the step,
        so callers only ever have to catch BlingError."""
        try:
            yield
        except PWTimeout as e:
            raise BlingError(f"timed out {what}; is a session open and the page clear?\n{e}") from e

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
        with self._step("loading browserling.com"):
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

        ``os`` is a slug (``"win10"``, ``"mac15"``, ``"ios18"``, ``"android15"``) or the name
        the menu shows (``"Windows 11"``). ``browser`` is a name with an optional version
        (``"firefox"``, ``"chrome138"``). Every call starts a fresh machine: files, the
        proxy and any tunnel from the previous one are gone.

        >>> s.open("example.com", os="win10", browser="firefox")
        'ready'
        """
        os = config.os_slug(os)
        try:
            config.os_family(os)
        except ValueError as e:
            raise BlingError(str(e)) from None
        self._dl_token = self._ul_token = None  # new session -> new tokens
        if "/browse/" in self.page.url:
            # A session is still live on this page. Browserling resumes it for any new
            # /browse/ address (seen 2026-11-25: asking for win11 gave back the win10
            # machine), so end it first or "a fresh machine" would be the old one.
            self._end_live_session()
        self.os = os
        self._target = target
        url = config.BROWSE.format(os=os, browser=browser, target=target)
        with self._step(f"loading {url}"):
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
        """End the session via the control panel (the context manager also cleans up).

        >>> s.end()
        """
        self._close_popups()
        self._open_menu("end")
        self.page.wait_for_timeout(500)

    def _end_live_session(self) -> None:
        """End the session this page is showing and wait until the page has left it. A
        session that is already dead (timed out, blocked) has no End button; that is fine."""
        try:
            self.end()
            self.page.wait_for_url(lambda u: "/browse/" not in u, timeout=10000)
        except (BlingError, PWTimeout):
            pass

    # --- control panel (outer DOM) -----------------------------------------
    def navigate(self, url: str, *, via: str = "panel") -> None:
        """Load a new URL in the running session.

        via="panel"  -> control-panel URL field (resets the view).
        via="remote" -> the remote browser's own address bar (keeps its DevTools open).

        >>> s.navigate("example.org")
        """
        if via not in ("panel", "remote"):
            raise BlingError(f"navigate(via={via!r}): via is 'panel' or 'remote'")
        if via == "remote":
            self.canvas_click(*config.REMOTE_ADDR_BAR)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.type(url, delay=20)
            self.page.keyboard.press("Enter")
        else:
            with self._step("loading a new address from the session menu"):
                self.page.locator("input.input.text-input").first.fill(url, timeout=4000)
                self.page.locator("button.button-go").first.click(timeout=4000)
        self._target = url
        self.page.wait_for_timeout(800)

    def set_resolution(self, value: str) -> None:
        """Set the remote screen size, e.g. ``"1920x1080"``.

        A size the list offers is clicked; any other size from 100 to 4096 a side goes
        through "Custom..." where the OS has it (Windows 10 and 11). The window stays
        open over the page afterwards: call ``dismiss()`` before a screenshot.

        >>> s.set_resolution("1280x1024")
        >>> s.set_resolution("1300x900")       # not in the list: set as a custom size
        """
        m = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", value.lower())
        if not m:
            raise BlingError(f"{value!r} is not a screen size; write it like '1280x1024'")
        w, h = m.groups()
        if self.platform == "ios":
            raise BlingError("iOS has no screen sizes; pick a device with set_device()")
        win = self._open_window("display", ".display-settings")
        with self._step(f"setting the screen to {w}x{h}"):
            preset = win.locator(f'.resolution[data-w="{w}"][data-h="{h}"] >> visible=true')
            if preset.count():
                preset.first.click(timeout=4000)
            else:
                custom = win.locator(".resolution[data-custom] >> visible=true")
                if not custom.count():
                    have = win.locator(".resolution[data-w] >> visible=true").all_inner_texts()
                    raise BlingError(
                        f"{w}x{h} is not offered on {self.os} and it has no custom sizes; "
                        f"offered: {', '.join(have) or 'none'}"
                    )
                custom.first.click(timeout=4000)
                pop = self.page.locator('.resolution-custom-popup[data-is-visible="true"]')
                pop.locator(".custom-w").fill(w, timeout=4000)
                pop.locator(".custom-h").fill(h)
                pop.locator("button.primary").click(timeout=4000)
        self.page.wait_for_timeout(400)

    def set_scale(self, percent: int) -> None:
        """Magnify the remote screen inside your page: 0 (fit, the default) to 100.

        This zooms and crops the view; it does not change the remote screen's size.

        >>> s.set_scale(40)
        >>> s.set_scale(0)
        """
        if not 0 <= int(percent) <= 100:
            raise BlingError(f"set_scale({percent!r}): scale runs from 0 to 100")
        item = "ios" if self.platform == "ios" else "display"
        win = self._open_window(item, ".display-settings")
        with self._step("moving the Screen Scale slider"):
            win.locator(".screen-scale input[type=range]").fill(str(int(percent)), timeout=4000)
        self.page.wait_for_timeout(400)
        self._close_window(".display-settings")

    # --- environment (OS, browser, apps) ------------------------------------
    def platforms(self) -> list[str]:
        """Every operating system the session menu offers, as it names them.

        >>> s.platforms()[:3]
        ['macOS 27', 'macOS 26', 'macOS 15']
        """
        return self._menu_lists()["platforms"]

    def browsers(self) -> dict[str, list[str]]:
        """The browsers this OS offers, each with the versions its list shows, newest first.

        The list shows each browser's newest 15 versions; ``set_browser`` reaches older
        ones by expanding it.

        >>> s.browsers()["Firefox"][:2]
        ['159', '158']
        """
        return self._menu_lists()["browsers"]

    def set_browser(
        self, name: str, version: str | int | None = None, *, ready_timeout: int = 60
    ) -> str:
        """Switch to another browser on the same machine and reopen the current address.

        The machine is kept, so its files, proxy and transfer links carry over. ``version``
        defaults to the newest the menu lists. Returns ``"ready"``.

        >>> s.set_browser("Firefox")
        'ready'
        >>> s.set_browser("Chrome", 120)
        'ready'
        """
        self._close_popups()
        want_version = None if version is None else str(version)
        with self._step(f"switching the browser to {name}"):
            self.page.locator("#main-menu .field.browser .select-dropdown").click(timeout=4000)
            for _ in range(_BROWSER_EXPANDS + 1):
                hit = self.page.evaluate(_BROWSER_PICK_JS, [name, want_version])
                if hit["state"] != "expand":
                    break
                self.page.locator("[data-bling-pick]").click(timeout=4000)
                self.page.wait_for_timeout(150)
            if hit["state"] != "found":
                self.page.keyboard.press("Escape")
                offered = hit.get("offered") or []
                if hit["state"] == "no-browser":
                    have = ", ".join(offered)
                    raise BlingError(f"no browser {name!r} on {self.os}; offered: {have}")
                span = f"; its versions run {offered[0]} down to {offered[-1]}" if offered else ""
                raise BlingError(f"no {name} {version} on {self.os}{span}")
            self.page.locator("[data-bling-pick]").click(timeout=4000)
            self.page.wait_for_function(
                "want => document.querySelector('#main-menu .field.browser .select-dropdown "
                ".select-name').textContent.trim().toLowerCase() === want",
                arg=f"{hit['name']} {hit['version']}".lower(),
                timeout=30000,
            )
        return self.wait_ready(ready_timeout)

    def set_os(self, os: str, *, browser: str | None = None, ready_timeout: int = 120) -> str:
        """Move the session to another operating system, at the address it is on now.

        Browserling starts a new machine for a new OS, so this is ``open()`` again: files,
        the proxy and any tunnel do not carry over. ``browser`` defaults to Chrome on
        Windows and Android and Safari on macOS and iOS. Returns ``"ready"``.

        >>> s.set_os("Windows 11")
        'ready'
        >>> s.set_os("mac15", browser="firefox")
        'ready'
        """
        if self._target is None:
            raise BlingError("set_os() needs an open session; call open() first")
        slug = config.os_slug(os)
        try:
            family = config.os_family(slug)
        except ValueError as e:
            raise BlingError(str(e)) from None
        return self.open(
            self._target,
            os=slug,
            browser=browser or config.DEFAULT_BROWSER[family],
            ready_timeout=ready_timeout,
        )

    def _menu_lists(self) -> dict:
        """The session menu's lists and current values; see _MENU_LISTS_JS."""
        try:
            return self.page.evaluate(_MENU_LISTS_JS)
        except Exception as e:  # noqa: BLE001 - no menu means no session
            raise BlingError(f"no session menu to read; call open() first ({e})") from None

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
            if here == "tunnel":  # "Your Own IP" has one button row
                row = ".buttons"
            self._click_visible(f".proxy-settings .screen-{here} {row} .do-cancel")
            self._await_proxy(lambda p: p["screen"] == "none" and not p["connected"])
            self.page.wait_for_timeout(_PROXY_SETTLE_MS)
            if self._proxy_state()["screen"] == "none":
                return
            here = self._proxy_state()["screen"] or here
        raise PWTimeout(f"the proxy panel went back to the {here} screen after Disconnect")

    def own_ip_tunnel(self) -> str:
        """Start the "Your Own IP" proxy and return the SSH command that brings it up.

        Run the command on your own machine and keep it running: the session then browses
        through your connection, so sites see your real address and the session can open
        ``localhost`` on your machine. ``proxy_status()`` shows when it is connected. The
        command carries a per-session secret; treat it like a password.

        >>> cmd = s.own_ip_tunnel()            # doctest: +SKIP
        >>> cmd.split("@")[1]
        'tunnel.browserling.com'
        """
        try:
            self._take_popups()
            self._open_proxy_panel()
            self._await_proxy(lambda p: not p["busy"])
            here = self._proxy_state()["screen"]
            if here not in ("none", "tunnel"):
                self._leave_proxy_screen(here)
            if self._proxy_state()["screen"] == "none":
                self._click_visible(".proxy-settings .use-btn.use-tunnel")
                self._await_proxy(lambda p: p["screen"] == "tunnel")
            box = ".proxy-settings .screen-tunnel .copy-command input"
            self.page.wait_for_function(
                "css => (document.querySelector(css) || {}).value", arg=box, timeout=_PROXY_STEP_MS
            )
            return self.page.locator(box).input_value()
        except (PWTimeout, _ProxyRefused) as e:
            raise BlingError(
                f"could not open the Your Own IP proxy.\npanel: {self._proxy_state()}\n{e}"
            ) from None

    def proxy_status(self) -> dict:
        """What the session is browsing through right now.

        Returns ``{"kind": ..., "status": ..., "connected": bool}``: ``kind`` is the
        panel's label ("Not using proxy", "Datacenter IP", ...) and ``status`` its whole
        status line, with the exit address and the network that owns it.

        >>> s.proxy_status()["kind"]
        'Not using proxy'
        """
        was_open = self._proxy_state()["visible"]
        try:
            self._open_proxy_panel()
        except PWTimeout as e:
            raise BlingError(f"could not open the proxy panel: {e}") from e
        state = self._proxy_state()
        if not was_open:
            self._close_window(".proxy-settings")
        return {
            "kind": state["kind"],
            "status": state["status"],
            "connected": bool(state["connected"]),
        }

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
        """Write raw bytes as a file in the VM's Downloads. Returns its name.

        >>> s.upload_bytes(bytes([0, 1, 2]), "blob.bin")
        'blob.bin'
        """
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
        polled out via the curl egress. On Windows keep ``command`` short (Win+R caps ~255
        chars, no embedded double-quotes); for more, upload a script and run it by path.
        On a Mac VM the command is a zsh line typed into Terminal, with neither limit.

        >>> s.run("whoami")
        'win10\\\\user'
        """
        tag = uuid.uuid4().hex[:8]
        log = f"_blrun_{tag}.log"
        marker = f"__DONE_{tag}__"
        if self.is_mac:
            dl = f"~/Downloads/{log}"
            launch = f"({command}) > {dl} 2>&1; echo {marker} >> {dl}"
        else:
            if '"' in command:
                raise BlingError(
                    "command must not contain double-quotes for Win+R routing — "
                    "upload a script and run it by path instead"
                )
            dl = rf"%USERPROFILE%\Downloads\{log}"
            launch = f'cmd /c "({command}) > {dl} 2>&1 & echo {marker}>> {dl}"'
            if len(launch) > 255:
                raise BlingError("command too long for Win+R — upload a script and run it by path")
        server, token = self.transfer_token()  # cache before launch; poll is HTTP-only
        self._launch(launch, delay=8)
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
        (a filename it creates in Downloads). On a Mac VM the script is a .sh/.zsh (run
        with zsh) or a .py (run with python3), started in the background from Terminal so
        the window is free for the next ``run()``.

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
        if self.is_mac:
            _check_vm_name(remote_name)  # typed into zsh unquoted
            runner = "python3" if remote_name.endswith(".py") else "zsh"
            self._launch(f"nohup {runner} ~/Downloads/{remote_name} > /dev/null 2>&1 &")
        else:
            self._launch(rf"cmd /c %USERPROFILE%\Downloads\{remote_name}")
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
        if self.is_mac:
            self.run("rm -f " + " ".join(f"~/Downloads/{n}" for n in remote_names))
        else:
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

    def focus_vm(self, point: tuple[int, int] | None = None) -> None:
        """Give the VM keyboard focus so OS shortcuts (Win+R) forward. No side effects."""
        if point is None:
            point = config.REMOTE_FOCUS_MAC if self.is_mac else config.REMOTE_FOCUS
        self.page.mouse.click(*point)
        self.page.wait_for_timeout(250)

    def launch_app(self, name: str) -> None:
        """Open (or bring forward) an app from the session menu's "Launch apps" list.

        Windows 10 and 11 offer ``"Explorer"``, ``"Sysinternals"``, ``"Command Prompt"``,
        ``"PowerShell"`` (both as admin), ``"Ubuntu WSL"`` and ``"Kali Linux WSL"``; a Mac
        offers ``"Finder"``, ``"Terminal"`` and ``"Xcode"``. iOS and Android have none.
        ``apps()`` lists what this session offers.

        >>> s.launch_app("PowerShell")
        """
        if self.platform in ("ios", "android"):
            raise BlingError(f"{self.os} has no Launch apps menu; it is on Windows and Mac only")
        self._close_popups()
        self._open_menu("apps")
        item = self.page.locator(".option-apps .option-version", has_text=name).first
        if not item.count():
            have = self.page.locator(".option-apps .option-version").all_inner_texts()
            self._close_popups()
            raise BlingError(f"no app {name!r} in Launch apps; offered: {', '.join(have)}")
        with self._step(f"launching {name}"):
            item.click(timeout=4000)
        self.page.wait_for_timeout(2500)  # cold start; an app already open just comes forward

    def apps(self) -> list[str]:
        """The apps ``launch_app`` can open in this session.

        >>> s.apps()[:2]
        ['Explorer', 'Sysinternals']
        """
        return self._menu_lists()["apps"]

    def _launch(self, line: str, *, delay: int = 10) -> None:
        """Type one command line into the VM and press Enter: the Win+R box on Windows,
        Terminal on a Mac. Output is the caller's to redirect; nothing is read back here."""
        if self.is_mac:
            # Terminal comes forward even if an upload left Finder's Downloads on top.
            self.launch_app("Terminal")
            self.focus_vm()
            # The first key after focusing is dropped, so spend it on a harmless space
            # (seen 2026-09-30: "(whoami ..." arrived as "whoami ..." and failed to parse).
            self.page.keyboard.press("Space")
            self.page.wait_for_timeout(300)
        else:
            self.focus_vm()
            self.page.keyboard.press("Meta+r")  # Win+R
            self.page.wait_for_timeout(900)
        self.page.keyboard.type(line, delay=delay)
        self.page.keyboard.press("Enter")

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

    def screenshot(self, path, *, remote_only: bool = False) -> Path:
        """Save a PNG of the session: the streamed remote view with the session menu and
        any open window over it. ``remote_only=True`` saves the remote screen alone.

        >>> s.screenshot("shot.png")
        WindowsPath('shot.png')
        >>> s.screenshot("page.png", remote_only=True)
        WindowsPath('page.png')
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not remote_only:
            self.page.screenshot(path=str(path))
            return path
        # The menu and every Browserling window float over the canvas; hide them for the
        # shot instead of closing them, so the session is left exactly as it was.
        hide = self.page.add_style_tag(
            content="#main-menu,.interactive-window,#group-popups{visibility:hidden!important}"
        )
        try:
            with self._step("taking a picture of the remote screen"):
                self.page.locator("#browser-screen canvas").first.screenshot(
                    path=str(path), timeout=10000
                )
        finally:
            hide.evaluate("e => e.remove()")
        return path

    def dismiss(self) -> None:
        """Close any open control-panel dialog/popup and return keyboard focus to the VM.
        Call this after file-transfer ops before sending VM keystrokes."""
        win = self.page.locator(".file-manager.interactive-window")
        if win.count() and win.first.is_visible():
            self.page.locator(f".button-item.{config.MENU_ITEMS['files']}").first.click()
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

    # --- clipboard ------------------------------------------------------------
    def set_clipboard(self, text: str) -> None:
        """Put ``text`` on the remote machine's clipboard (then paste it with ``key``).

        >>> s.set_clipboard("hello from outside")
        >>> s.key("Control+V")
        """
        win = self._open_clipboard()
        with self._step("sending text to the remote clipboard"):
            win.locator("textarea").fill(text, timeout=4000)
            win.locator("button.send").click(timeout=4000)
        self.page.wait_for_timeout(500)
        self._close_window(".clipboard-manager")

    def get_clipboard(self) -> str:
        """The text on the remote machine's clipboard, as the Clipboard Manager shows it.

        Text copied in the session reaches the manager a second or two later. Your own
        clipboard is never read or written: bling grants the page no clipboard permission.

        >>> s.get_clipboard()
        'copied inside the session'
        """
        win = self._open_clipboard()
        self.page.wait_for_timeout(600)
        with self._step("reading the Clipboard Manager"):
            text = win.locator("textarea").input_value(timeout=4000)
        self._close_window(".clipboard-manager")
        return text

    def _open_clipboard(self):
        win = self._open_window("clipboard", ".clipboard-manager")
        if win.locator(".mini-popup >> visible=true").count():
            self._close_window(".clipboard-manager")
            raise BlingError(f"Browserling has no copy and paste on {self.os} yet")
        return win

    # --- co-browsing ----------------------------------------------------------
    def share(self, name: str = "bling", *, view_only: bool = False) -> str:
        """Start co-browsing and return the invite link. Anyone holding the link can join
        the live session, so share it as you would a password.

        ``name`` is shown to guests. ``view_only=True`` lets guests watch until you hand
        them control; the default lets them take control at any time. Calling it again
        while sharing returns the link already in force.

        >>> s.share("Analyst", view_only=True)
        'https://browserling.com/join/Ab3dE5'
        """
        pop = self._open_share()
        with self._step("creating the co-browsing invite link"):
            screen = pop.get_attribute("data-screen")
            if screen == "guest":
                raise BlingError("this session was joined as a guest; only its host can share it")
            if screen != "host":
                pop.locator("#cobrowse-name").fill(name, timeout=4000)
                pop.locator("#cobrowse-trust-0" if view_only else "#cobrowse-trust-1").check()
                pop.locator(".btn-create").click(timeout=4000)
            self.page.wait_for_function(
                "() => (document.querySelector('#cobrowse-url') || {}).value", timeout=15000
            )
            link = pop.locator("#cobrowse-url").input_value()
            pop.locator(".if-host .btn-close").click(timeout=4000)
        return link

    def stop_sharing(self) -> None:
        """Stop co-browsing: the invite link dies and every guest is removed. Safe to call
        when nothing is shared.

        >>> s.stop_sharing()
        """
        pop = self._open_share()
        with self._step("stopping co-browsing"):
            if pop.get_attribute("data-screen") == "host":
                pop.locator(".if-host .btn-end").click(timeout=4000)
                # "Are you sure?": the confirm button is the popup's primary one.
                self.page.locator('.popup.tiny[data-is-visible="true"] button.primary').first.click(
                    timeout=4000
                )
                self.page.wait_for_function(
                    "() => document.querySelector('#popup-cobrowsing').dataset.screen !== 'host'",
                    timeout=15000,
                )
        self._take_popups()

    def _open_share(self):
        self._take_popups()
        self._open_menu("share")
        pop = self.page.locator("#popup-cobrowsing")
        try:
            pop.wait_for(state="visible", timeout=4000)
        except PWTimeout as e:
            raise BlingError(f"the Co-browsing popup did not open: {e}") from e
        return pop

    # --- local testing (tunnel to your own web server) -------------------------
    def open_tunnel(self, address: str = "localhost:8080", *, client: str = "ssh") -> str:
        """Open a Local Testing tunnel to ``address`` and return the command that connects it.

        Run the command on the machine that can reach ``address`` and keep it running; the
        session can then open that address. ``client`` picks the command's form: ``"ssh"``
        (OpenSSH 7.6 and later), ``"old-ssh"`` or ``"putty"``. The command carries a
        per-session secret. An open tunnel is closed first, so calling this again moves it.

        >>> s.open_tunnel("localhost:8080")          # doctest: +SKIP
        'ssh -R 2 <id>:<secret>@tunnel.browserling.com'
        """
        if client not in config.TUNNEL_CLIENTS:
            raise BlingError(
                f"open_tunnel(client={client!r}): one of {', '.join(config.TUNNEL_CLIENTS)}"
            )
        win = self._open_window("local", ".tunnels-setup")
        box = ".tunnels-setup .run-command .copy-command input"
        with self._step(f"opening a tunnel to {address}"):
            if win.get_attribute("data-screen") == "run-command":
                self._tunnel_close_click(win)
            win.locator(".open-tunnel input.user-input").fill(address, timeout=4000)
            win.locator(".open-tunnel button.primary").click(timeout=4000)
            self.page.wait_for_function(
                "css => (document.querySelector(css) || {}).value", arg=box, timeout=15000
            )
            if client != "ssh":
                label = config.TUNNEL_CLIENTS[client]
                win.locator(".run-command .top a", has_text=label).first.click(timeout=4000)
                self.page.wait_for_timeout(300)
            command = self.page.locator(box).input_value()
        self._close_window(".tunnels-setup")
        return command

    def tunnel_stats(self) -> dict:
        """The Local Testing tunnel's state: whether one is open, its status line, and the
        traffic it has carried.

        >>> s.tunnel_stats()["status"]
        'Waiting for connection...'
        """
        win = self._open_window("local", ".tunnels-setup")
        with self._step("reading the tunnel's counters"):
            is_open = win.get_attribute("data-screen") == "run-command"
            status = win.locator(".run-command .status").text_content(timeout=4000) or ""
            stats = {
                "open": is_open,
                "status": status.strip() if is_open else "",
                "requests": int(win.locator(".lt-stats-req").text_content() or 0),
                "in": (win.locator(".lt-stats-in").text_content() or "").strip(),
                "out": (win.locator(".lt-stats-out").text_content() or "").strip(),
            }
        self._close_window(".tunnels-setup")
        return stats

    def close_tunnel(self) -> None:
        """Close the Local Testing tunnel. Safe to call when none is open.

        >>> s.close_tunnel()
        """
        win = self._open_window("local", ".tunnels-setup")
        with self._step("closing the tunnel"):
            if win.get_attribute("data-screen") == "run-command":
                self._tunnel_close_click(win)
        self._close_window(".tunnels-setup")

    def _tunnel_close_click(self, win) -> None:
        win.locator(".run-command button.primary").click(timeout=4000)
        self.page.wait_for_function(
            "() => document.querySelector('.tunnels-setup').dataset.screen === 'open-tunnel'",
            timeout=10000,
        )

    # --- iOS ------------------------------------------------------------------
    def devices(self) -> list[str]:
        """iOS only: every iPhone and iPad model this iOS version offers.

        >>> s.devices()[:2]
        ['iPhone 16e', 'iPhone 16 Plus']
        """
        self._need_ios("devices()")
        return self._menu_lists()["devices"]

    def set_device(self, model: str, *, ready_timeout: int = 180) -> str:
        """iOS only: restart the session as another device model, e.g. ``"iPad (A16)"``.

        Browserling restarts iOS on the new device; files and apps on the old one are not
        copied over. Returns ``"ready"``.

        >>> s.set_device("iPhone 15")
        'ready'
        """
        self._need_ios("set_device()")
        lists = self._menu_lists()
        if model not in lists["devices"]:
            raise BlingError(
                f"no device {model!r} on {self.os}; offered: {', '.join(lists['devices'])}"
            )
        if lists["device"] == model:
            return "ready"
        self._close_popups()
        with self._step(f"switching the device to {model}"):
            self.page.locator("#main-menu .field.model .select-dropdown").click(timeout=4000)
            self.page.locator("#main-menu .field.model .option-iosmodel").get_by_text(
                model, exact=True
            ).first.click(timeout=4000)
            # "Set device model ... Proceed?": Change model is the popup's primary button.
            self.page.locator('.popup[data-is-visible="true"] button.primary').first.click(
                timeout=4000
            )
            self.page.wait_for_function(
                "want => { const m = document.querySelector('#main-menu .field.model');"
                " return m.querySelector('.select-dropdown .select-name').textContent.trim()"
                " === want && /ready/i.test(m.querySelector('.field-iosstatus').textContent); }",
                arg=model,
                timeout=ready_timeout * 1000,
            )
        return self.wait_ready(ready_timeout)

    def set_orientation(self, degrees: int) -> None:
        """iOS only: rotate the device to 0 (portrait), 90, 180 or 270 degrees.

        >>> s.set_orientation(90)
        """
        self._need_ios("set_orientation()")
        if degrees not in config.IOS_ORIENTATIONS:
            raise BlingError(f"set_orientation({degrees!r}): one of 0, 90, 180, 270")
        win = self._open_window("ios", ".display-settings")
        with self._step(f"rotating the device to {degrees} degrees"):
            win.locator(f'.orient[data-r="{config.IOS_ORIENTATIONS[degrees]}"]').click(timeout=4000)
        self.page.wait_for_timeout(1500)  # the stream re-lays itself out
        self._close_window(".display-settings")

    def press_button(self, name: str) -> None:
        """iOS only: press a device button: ``"home"``, ``"action"``, ``"mute"``,
        ``"volume_down"``, ``"volume_up"`` or ``"lock"``.

        >>> s.press_button("home")
        """
        self._need_ios("press_button()")
        if name not in config.IOS_BUTTONS:
            raise BlingError(f"press_button({name!r}): one of {', '.join(config.IOS_BUTTONS)}")
        win = self._open_window("ios", ".display-settings")
        with self._step(f"pressing the {name} button"):
            win.locator(f'.ios-btn[data-key="{config.IOS_BUTTONS[name]}"]').click(timeout=4000)
        self.page.wait_for_timeout(600)
        self._close_window(".display-settings")

    def show_devtools(self) -> None:
        """iOS only: open Safari's Web Inspector for the page, in a Browserling window over
        the session. On desktop sessions use the browser's own tools: ``s.key("F12")``.

        >>> s.show_devtools()
        """
        self._need_ios("show_devtools()")
        self._close_popups()
        self._open_menu("devtools")
        self.page.wait_for_timeout(1500)

    def _need_ios(self, what: str) -> None:
        if self.platform != "ios":
            raise BlingError(f"{what} is for iOS sessions; open one with os='ios18'")

    # --- internals ----------------------------------------------------------
    def _open_window(self, item: str, css: str):
        """Open one of the session menu's windows (if it is not open) and return it."""
        win = self.page.locator(css).first
        # data-is-visible is the page's own flag; a window fading out still looks visible.
        if not (win.count() and win.get_attribute("data-is-visible") == "true"):
            self._close_popups()
            self._open_menu(item)
            try:
                self.page.locator(f'{css}[data-is-visible="true"]').first.wait_for(
                    state="visible", timeout=4000
                )
            except PWTimeout as e:
                raise BlingError(f"the {item} window ({css}) did not open: {e}") from e
        return win

    def _close_window(self, css: str) -> None:
        """Close a session-menu window with its own × if it is open."""
        shut = self.page.locator(f"{css} .close").first
        try:
            if shut.count() and shut.is_visible():
                shut.click(timeout=1500)
                self.page.locator(f'{css}[data-is-visible="true"]').first.wait_for(
                    state="hidden", timeout=2000
                )
        except Exception:  # noqa: BLE001 - a window that will not close is not fatal
            pass

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
