"""Environment specifics, isolated here so the brittle bits are easy to re-pin.

Everything that could change when Browserling changes (URLs, the VM's browser paths, the
fixed viewport that keeps canvas coordinates stable, menu selectors, block-screen names)
lives in this one file — never inline in the logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import ViewportSize

HOME = "https://www.browserling.com/"
BROWSE = "https://www.browserling.com/browse/{os}/{browser}/{target}"

# Persistent Chrome profile that holds the logged-in cookie (see Session / login()).
PROFILE = str(Path.home() / ".browserling_pw_profile")

# Fixed so the remote canvas renders at known coordinates run to run.
VIEWPORT: ViewportSize = {"width": 1500, "height": 850}

# Browsers on the Browserling Windows VM live under D:\browsers\<name>\<version>\.
FIREFOX_EXE = r"D:\browsers\firefox\firefox-140\firefox.exe"

# Inner-browser click points, page CSS coords @1500x850 (verified live).
REMOTE_ADDR_BAR = (450, 103)  # remote browser's own address bar
REMOTE_FOCUS = (760, 60)  # tab strip — click to give the VM keyboard focus (Win+R etc.)

# Common OS/browser slugs for the /browse/<os>/<browser>/<url> convention (picker has more).
OS_SLUGS = ("win10", "win11", "win7", "mac", "android", "ios")
BROWSER_SLUGS = ("chrome138", "firefox", "edge", "safari", "ie11", "opera")

# Floating-menu items: name -> `.button-item.<slug>`.
MENU_ITEMS = {
    "display": "set-resolution",
    "capture": "capture-screen",
    "proxy": "proxy-and-vpn",
    "files": "file-transfer",
    "devtools": "show-devtools",
    "share": "share-browser",
    "end": "end-session",
}

# Block/queue screens: still-coming-up vs dead. wait_ready() uses these.
TRANSIENT = ("screen-progress", "screen-resuming", "screen-premium-connecting", "screen-loading")
FATAL = {
    "screen-free-limit": "free daily limit reached",
    "screen-dev-limit": "developer-plan limit reached",
    "screen-team-limit": "team limit reached",
    "screen-monthly-limit": "monthly limit reached",
    "screen-invalid-request": "invalid OS/browser/url request",
    "times-up": "session time is up",
    "idle-timeout": "session idle-timed-out (~10 min)",
    "screen-color-limit": "plan limit reached",
    "vm-unavailable": "no VM available",
    "vm-shutdown": "VM shut down",
    "cant-reconnect": "cannot reconnect to session",
    "browser-connect-error": "browser connect error",
    "browser-websocket-error": "browser websocket error",
    "browser-firewall-error": "firewall blocked the connection",
    "queue-firewall-error": "firewall blocked the connection",
    "new-host-joined": "another host joined (duplicate session)",
}
