"""bling — drive a Browserling sandbox from Python. HAR capture for humans (and agents).

>>> import bling
>>> h = bling.har("demo.browserling.com", out="demo.har")
>>> h
<HAR 'demo.browserling.com' 7 entries, creator Firefox>

One-time setup: ``bling login`` (you solve the reCAPTCHA once; the cookie persists).
"""

from __future__ import annotations

from .errors import (
    BlingError,
    EgressError,
    NotLoggedIn,
    NotReady,
    SessionBlocked,
    Timeout,
)
from .har import HAR, arm_capture, capture, capture_count, capture_here, sweep_captures
from .session import Session, login

__version__ = "0.1.0"


def har(
    url: str,
    *,
    out=None,
    os: str = "win10",
    timeout: int = 45,
    live: bool = False,
    profile: str | None = None,
) -> HAR:
    """Capture a URL's HAR from a real Firefox in a Browserling sandbox — the one-liner.

    Real browser, no CDP/automation, isolated VM. Uses the persistent login cookie, so run
    ``bling login`` once first. Pass ``out`` to also write the .har to disk. Runs headless by
    default; pass ``live=True`` to watch the browser window. (The captured browser is always
    Firefox; ``os`` selects the Windows VM.)

    >>> import bling
    >>> h = bling.har("demo.browserling.com")
    >>> len(h), h.creator
    (7, 'Firefox')
    """
    with Session(profile=profile, headless=not live) as s:
        s.require_login()
        result = capture(s, url, os=os, timeout=timeout)
    if out is not None:
        result.save(out)
    return result


__all__ = [
    "har",
    "capture",
    "capture_here",
    "arm_capture",
    "sweep_captures",
    "capture_count",
    "login",
    "Session",
    "HAR",
    "BlingError",
    "NotLoggedIn",
    "SessionBlocked",
    "NotReady",
    "Timeout",
    "EgressError",
    "__version__",
]
