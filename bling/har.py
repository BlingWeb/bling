"""Capture a URL's HAR from a REAL Firefox inside a Browserling sandbox.

No CDP, no automation flags, no AV-flagged tools — the suspect URL loads in a normal Firefox
on the disposable VM. We launch Firefox from its VM path with a user.js that turns on the
built-in netmonitor HAR auto-export, open the netmonitor, navigate, and egress the
auto-written HAR. (Real-browser capture matters: automation is fingerprintable and malware
will cloak if it detects it.)
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from . import config
from .errors import BlingError, Timeout

if TYPE_CHECKING:
    from .session import Session

# The auto-export writes this fixed filename to the VM Downloads (matches defaultFileName).
_EXPORT_HAR = "blhar.har"

# Firefox profile prefs that enable the built-in netmonitor HAR auto-export to disk.
_USERJS = r"""user_pref("devtools.netmonitor.har.enableAutoExportToFile", true);
user_pref("devtools.netmonitor.har.includeResponseBodies", true);
user_pref("devtools.netmonitor.har.forceExport", true);
user_pref("devtools.netmonitor.har.defaultLogDir", "C:\\Users\\user\\Downloads");
user_pref("devtools.netmonitor.har.defaultFileName", "blhar");
user_pref("devtools.netmonitor.har.pageLoadedTimeout", 2500);
user_pref("devtools.toolbox.selectedTool", "netmonitor");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
"""

_LAUNCH_TMPL = r"""@echo off
set DL=%USERPROFILE%\Downloads
del /q "%DL%\*.har" 2>nul
del /q "%DL%\__DONE__" 2>nul
rd /s /q C:\cap\ffprof 2>nul
mkdir C:\cap\ffprof
copy /Y "%DL%\user.js" C:\cap\ffprof\user.js
start "" "__FF__" -no-remote -new-instance -profile C:\cap\ffprof about:blank
echo OK> "%DL%\__DONE__"
"""


def _render_scripts(tag: str) -> dict[str, str]:
    """Build the per-capture VM scripts, uuid-tagged so repeat captures never collide on a
    stale sentinel. Returns the file contents plus the tagged sentinel name.

    >>> s = _render_scripts("abcd1234")
    >>> "__FF__" in s["launch.bat"] or "__DONE__" in s["launch.bat"]   # placeholders rendered
    False
    >>> s["launch_done"]
    'launch_abcd1234.done'
    """
    launch_done = f"launch_{tag}.done"
    launch = _LAUNCH_TMPL.replace("__FF__", config.FIREFOX_EXE).replace("__DONE__", launch_done)
    return {"user.js": _USERJS, "launch.bat": launch, "launch_done": launch_done}


class HAR:
    """A captured HTTP Archive — a thin, introspectable wrapper over the HAR dict.

    >>> h.creator, len(h)
    ('Firefox', 7)
    >>> h.urls()[0]
    'https://demo.browserling.com/'
    >>> h.save("demo.har")
    """

    def __init__(self, data: dict, url: str):
        self.data = data
        self.url = url

    @classmethod
    def load(cls, path) -> HAR:
        """Read a ``.har`` file from disk (the inverse of ``save``).

        The source URL isn't stored in a HAR, so ``url`` is recovered from the first entry
        (the page the capture navigated to), falling back to the filename.

        >>> h = HAR.load("demo.har")   # doctest: +SKIP
        """
        path = Path(path)
        try:
            data = json.loads(path.read_bytes())  # json detects the encoding
        except OSError as e:
            raise BlingError(f"cannot read HAR {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise BlingError(f"{path} is not valid HAR JSON: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("log", {}).get("entries"), list):
            raise BlingError(f"{path} is valid JSON but not a HAR (no log.entries list)")
        try:
            first = data["log"]["entries"][0]["request"]["url"]
        except (KeyError, IndexError, TypeError):
            first = path.name
        return cls(data, first)

    @property
    def entries(self) -> list[dict]:
        """The request/response entries (each a HAR entry dict)."""
        return self.data["log"]["entries"]

    @property
    def creator(self) -> str:
        """The tool that produced the HAR (e.g. 'Firefox')."""
        return self.data["log"]["creator"]["name"]

    def urls(self) -> list[str]:
        """Every requested URL, in order."""
        return [e["request"]["url"] for e in self.entries]

    def save(self, path) -> Path:
        """Write the HAR JSON to ``path`` (overwrites, like ``open(path, 'w')``). Returns it."""
        path = Path(path)
        path.write_text(json.dumps(self.data), encoding="utf-8")
        return path

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return f"<HAR {self.url!r} {len(self)} entries, creator {self.creator}>"


def capture(session: Session, url: str, *, os: str = "win10", timeout: int = 45) -> HAR:
    """Capture ``url``'s HAR using ``session`` (which must already be logged in).

    Note: this **opens a fresh session** internally (calls ``session.open``), so don't open
    one yourself first — it would be replaced, and any proxy you set would be lost. To keep a
    proxy (or other session state), use ``capture_here`` on an already-open session instead.

    After navigating we poll for the auto-exported HAR until it appears and its size settles
    (robust to slow pages, fast for quick ones); ``timeout`` bounds that wait.

    >>> with bling.Session() as s:
    ...     s.require_login()
    ...     h = bling.capture(s, "demo.browserling.com")
    """
    session.open("example.com", os=os, browser="firefox")  # benign start; our own FF loads it
    return _run_capture(session, url, timeout=timeout)


def capture_here(session: Session, url: str, *, timeout: int = 45) -> HAR:
    """Capture ``url``'s HAR on the session that's **already open** — keeping its proxy and state.

    Unlike ``capture`` / ``bling.har`` (which open their own fresh session and so drop any proxy),
    this launches the instrumented Firefox in the VM you already have. Use it to record how a page
    behaves through a chosen proxy country — set the proxy, then capture, then switch and capture
    again — which is how you catch a site that cloaks its content by geography.

    >>> with bling.Session() as s:                       # doctest: +SKIP
    ...     s.require_login()
    ...     s.open("example.com", browser="firefox")
    ...     s.set_proxy("datacenter", country="germany")
    ...     h = bling.capture_here(s, "https://example.com")
    """
    if session.page is None:
        raise BlingError("no open session — call session.open(...) before capture_here")
    return _run_capture(session, url, timeout=timeout)


def arm_capture(session: Session) -> None:
    """Launch an instrumented Firefox in the VM with its network monitor recording, so that
    **every** page it loads from now writes its own HAR to the VM's Downloads (Firefox
    auto-numbers them: blhar.har, blhar-1.har, ...). Drive that Firefox, switch proxies freely,
    then collect the HARs with ``sweep_captures``.

    The session must already be open. The instrumented Firefox is separate from the streamed
    browser, so drive it by keyboard (``Control+l`` for its address bar) and pixel clicks.
    """
    if session.page is None:
        raise BlingError("no open session — call session.open(...) before arm_capture")
    scripts = _render_scripts(uuid.uuid4().hex[:8])
    session.page.wait_for_timeout(5000)
    session.upload_text(scripts["user.js"], "user.js")
    session.upload_text(scripts["launch.bat"], "launch.bat")
    session.transfer_token()  # cache egress token before VM keystrokes
    session.dismiss()  # close the file dialog + reclaim VM focus
    session.run_script("launch.bat", sentinel=scripts["launch_done"], timeout=60)
    session.page.wait_for_timeout(9000)  # let Firefox open
    session.focus_vm()
    session.key("Control+Shift+E")  # netmonitor on -> auto-export fires on every page load
    session.page.wait_for_timeout(2500)


def _run_capture(session: Session, url: str, *, timeout: int = 45) -> HAR:
    """Single-page capture: arm the instrumented Firefox, navigate to one URL, egress its HAR."""
    arm_capture(session)
    session.focus_vm()
    session.key("Control+l")  # focus the address bar
    session.page.wait_for_timeout(600)
    session.type(url)
    session.key("Enter")

    tmp = Path(tempfile.gettempdir()) / f"bling_{uuid.uuid4().hex[:8]}.har"
    try:
        try:
            session.download_when_ready(_EXPORT_HAR, tmp, timeout=timeout)
        except Timeout as e:
            raise BlingError(
                f"no HAR was produced for {url!r} within {timeout}s — the page may not have "
                f"fired its load event, or Firefox failed to launch/export (check the VM)"
            ) from e
        data = json.loads(tmp.read_bytes())  # json detects the encoding
        return HAR(data, url)
    finally:
        tmp.unlink(missing_ok=True)  # don't leave hostile bodies in /tmp


def _har_index(name: str) -> int:
    """Load order from the auto-uniquified name: blhar.har -> 0, blhar-1.har -> 1, ..."""
    m = re.search(r"blhar-(\d+)\.har$", name, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _capture_names(session: Session) -> list[str]:
    """The HAR files the armed Firefox has written, in load order."""
    out = session.run(r"dir /b C:\Users\user\Downloads\blhar*.har")
    names = [ln.strip() for ln in out.splitlines() if ln.strip().lower().endswith(".har")]
    names.sort(key=_har_index)
    return names


def capture_count(session: Session) -> int:
    """How many page HARs the armed Firefox has written so far (used to tag by proxy)."""
    return len(_capture_names(session))


def sweep_captures(session: Session) -> list[HAR]:
    """Download every HAR the armed Firefox has written, oldest page first (see ``arm_capture``).
    Each returned HAR's ``url`` is recovered from its own first request.
    """
    result: list[HAR] = []
    for name in _capture_names(session):
        tmp = Path(tempfile.gettempdir()) / f"bling_{uuid.uuid4().hex[:8]}.har"
        try:
            session.download(name, tmp)
            data = json.loads(tmp.read_bytes())
            try:
                url = data["log"]["entries"][0]["request"]["url"]
            except (KeyError, IndexError, TypeError):
                url = name
            result.append(HAR(data, url))
        finally:
            tmp.unlink(missing_ok=True)
    return result
