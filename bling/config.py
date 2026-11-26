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
# On a Mac VM the same click lands on the empty right half of the macOS menu bar, which
# gives the canvas focus without switching apps (verified live 2026-09-30, macOS 15.2).
REMOTE_FOCUS_MAC = (1000, 76)

# The Mac VM has no Win+R equivalent that forwards (Cmd+Space never reaches Spotlight),
# so commands go through Terminal, opened from the control panel's "Launch apps" menu.
MAC_OS_SLUGS = ("mac",)  # kept for callers; Session.platform is the test to use

# An OS is addressed as <family><version> in /browse/<os>/<browser>/<url>: win10, win8.1,
# winxp, mac15, ios18, android15 (the page builds it the same way; read from its code
# 2026-11-25). A bare family such as "mac" opens that family's default version.
OS_FAMILIES = ("win", "mac", "ios", "android")
_OS_LONG_NAMES = (("windows", "win"), ("macos", "mac"))
# The browser a family opens with when the caller names none (Session.set_os).
DEFAULT_BROWSER = {"win": "chrome", "mac": "safari", "ios": "safari", "android": "chrome"}

# Common OS/browser slugs, as examples. Session.platforms() and Session.browsers() read the
# full, current lists from a live session's menu.
OS_SLUGS = ("win10", "win11", "win7", "mac", "android", "ios")
BROWSER_SLUGS = ("chrome138", "firefox", "edge", "safari", "ie11", "opera")


def os_slug(name: str) -> str:
    """Turn an OS name as the menu shows it into its address slug; slugs pass through.

    >>> os_slug("Windows 11"), os_slug("macOS 15"), os_slug("iOS 18"), os_slug("win10")
    ('win11', 'mac15', 'ios18', 'win10')
    """
    slug = name.strip().lower().replace(" ", "")
    for long, short in _OS_LONG_NAMES:
        if slug.startswith(long):
            slug = short + slug[len(long) :]
    return slug


def os_family(slug: str) -> str:
    """The family of an OS slug: ``"win"``, ``"mac"``, ``"ios"`` or ``"android"``.

    >>> os_family("win8.1"), os_family("mac"), os_family("ios18")
    ('win', 'mac', 'ios')
    """
    for family in OS_FAMILIES:
        if slug.startswith(family):
            return family
    raise ValueError(f"{slug!r} is not an OS slug (it starts with one of {', '.join(OS_FAMILIES)})")


# Floating-menu items: name -> `.button-item.<slug>`.
MENU_ITEMS = {
    "display": "set-resolution",
    "ios": "ios-settings",  # iOS only, in place of "display"; opens the same window
    "capture": "capture-screen",
    "local": "local-testing",
    "keyboard": "show-keyboard",
    "proxy": "proxy-and-vpn",
    "clipboard": "copy-and-paste",
    "files": "file-transfer",
    "apps": "launch-apps",  # Windows and Mac; hidden on iOS and Android
    "devtools": "show-devtools",  # iOS only
    "share": "co-browsing",  # was "share-browser" until 2026-09
    "end": "end-session",
}

# iOS device buttons in the "iOS settings" window: our name -> the button's data-key.
IOS_BUTTONS = {
    "home": "Home",
    "action": "LaunchApplication1",
    "mute": "AudioVolumeMute",
    "volume_down": "AudioVolumeDown",
    "volume_up": "AudioVolumeUp",
    "lock": "Power",
}
# iOS screen rotation: degrees -> the orientation chip's data-r.
IOS_ORIENTATIONS = {0: "0", 90: "3", 180: "1", 270: "2"}

# Local Testing prints its SSH command for three clients; the two it is not showing are
# links, found by their text.
TUNNEL_CLIENTS = {"ssh": "new SSH", "old-ssh": "old SSH", "putty": "PuTTY"}

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
