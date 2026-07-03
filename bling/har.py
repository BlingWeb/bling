"""Capture a URL's HAR from a REAL Firefox inside a Browserling sandbox.

No CDP, no automation flags, no AV-flagged tools — the suspect URL loads in a normal Firefox
on the disposable VM. We launch Firefox from its VM path with a user.js that turns on the
built-in netmonitor HAR auto-export, open the netmonitor, navigate, and egress the
auto-written HAR. (Real-browser capture matters: automation is fingerprintable and malware
will cloak if it detects it.)
"""

from __future__ import annotations

import json
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
    one yourself first — it would be replaced. Prefer the top-level ``bling.har(url)`` for the
    one-liner; use this when you want to keep the same ``Session`` for further work afterward.

    After navigating we poll for the auto-exported HAR until it appears and its size settles
    (robust to slow pages, fast for quick ones); ``timeout`` bounds that wait.

    >>> with bling.Session() as s:
    ...     s.require_login()
    ...     h = bling.capture(s, "demo.browserling.com")
    """
    scripts = _render_scripts(uuid.uuid4().hex[:8])
    session.open(
        "example.com", os=os, browser="firefox"
    )  # benign start; our own FF loads the target
    session.page.wait_for_timeout(5000)
    session.upload_text(scripts["user.js"], "user.js")
    session.upload_text(scripts["launch.bat"], "launch.bat")
    session.transfer_token()  # cache egress token before VM keystrokes
    session.dismiss()  # close the file dialog + reclaim VM focus

    session.run_script("launch.bat", sentinel=scripts["launch_done"], timeout=60)
    session.page.wait_for_timeout(9000)  # let Firefox open

    session.focus_vm()
    session.key("Control+Shift+E")  # open the netmonitor (required for auto-export)
    session.page.wait_for_timeout(2500)
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
