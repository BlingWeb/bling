"""Live tests for the session-menu features: browser and OS switching, screen size and
scale, clipboard, apps, co-browsing, Local Testing, the Your Own IP proxy and iOS controls.

They drive real Browserling machines, so they are skipped unless ``BLING_LIVE=1`` and need a
logged-in profile (``bling login``). Three machines are started: Windows 10, Windows 11 and
iOS 18, then a Mac for one address check.

    BLING_LIVE=1 pytest tests/test_live_panel.py -x -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling
from bling.errors import BlingError

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def s():
    with bling.Session() as session:
        session.require_login()
        assert session.open("example.com") == "ready"
        yield session
        try:
            session.end()
        except BlingError:
            pass


# --- Windows 10: one machine for everything that keeps it ----------------------
def test_menu_lists(s):
    assert "Windows 10" in s.platforms() and "iOS 18" in s.platforms()
    assert {"Chrome", "Firefox", "Edge"} <= set(s.browsers())
    assert "PowerShell" in s.apps()


def test_set_browser_keeps_the_machine(s):
    token = s.transfer_token()
    assert s.set_browser("Firefox") == "ready"
    assert "/win10/firefox" in s.page.url
    s._dl_token = None  # read the link again from the page, not from the cache
    assert s.transfer_token() == token


def test_set_browser_reaches_an_old_version_and_refuses_a_missing_one(s):
    assert s.set_browser("chrome", 100) == "ready"  # past the 15 shown: needs expanding
    assert "/win10/chrome100/" in s.page.url
    with pytest.raises(BlingError, match="no browser 'Netscape'"):
        s.set_browser("Netscape")
    with pytest.raises(BlingError, match="no Chrome 9999"):
        s.set_browser("Chrome", 9999)
    assert s.set_browser("Chrome", 138) == "ready"


def test_resolution_preset_custom_and_scale(s, tmp_path):
    s.set_resolution("1280x1024")
    s.set_resolution("1300x900")
    active = s.page.locator(".display-settings .resolution[data-active='true']").inner_text()
    assert active.startswith("Custom")
    s.dismiss()
    s.set_scale(40)
    s.set_scale(0)
    shot = s.screenshot(tmp_path / "remote.png", remote_only=True)
    assert shot.stat().st_size > 5000


def test_clipboard_both_ways(s):
    s.dismiss()
    s.set_clipboard("bling clip in 123")
    assert s.run("powershell -c Get-Clipboard") == "bling clip in 123"
    s.run("echo bling-clip-out-456| clip")
    s.page.wait_for_timeout(3000)
    assert "bling-clip-out-456" in s.get_clipboard()


def test_launch_app_on_windows(s):
    s.launch_app("Explorer")
    with pytest.raises(BlingError, match="no app 'Solitaire'"):
        s.launch_app("Solitaire")
    s.dismiss()


def test_share_and_stop(s):
    link = s.share("bling-test", view_only=True)
    assert link.startswith("https://browserling.com/join/")
    assert s.share() == link  # already sharing: the same link comes back
    s.stop_sharing()
    s.stop_sharing()  # nothing shared: a no-op
    s.dismiss()


def test_tunnel_commands(s):
    assert s.tunnel_stats()["open"] is False
    cmd = s.open_tunnel("localhost:8080")
    assert cmd.startswith("ssh -R ") and cmd.endswith("@tunnel.browserling.com")
    assert s.open_tunnel("localhost:8081", client="putty").startswith("putty -ssh -R ")
    stats = s.tunnel_stats()
    assert stats["open"] is True and stats["requests"] == 0
    s.close_tunnel()
    assert s.tunnel_stats()["open"] is False


def test_own_ip_then_a_normal_proxy(s):
    assert s.proxy_status()["kind"] == "Not using proxy"
    assert s.own_ip_tunnel().endswith("@tunnel.browserling.com")
    s.set_proxy("datacenter", country="Germany")  # must back out of the Your Own IP screen
    status = s.proxy_status()
    assert status["connected"] and status["kind"].lower().startswith("datacenter")
    s.dismiss()


# --- other operating systems: a new machine each --------------------------------
def test_set_os_windows_11(s):
    assert s.set_os("Windows 11") == "ready"
    assert s.os == "win11" and "/browse/win11/" in s.page.url
    assert s.proxy_status()["kind"] == "Not using proxy"  # a new machine: the proxy is gone


def test_ios_controls(s):
    assert s.set_os("iOS 18") == "ready"
    assert s.platform == "ios" and "/browse/ios18/" in s.page.url
    assert "iPhone 15" in s.devices()
    s.set_orientation(90)
    s.set_orientation(0)
    s.press_button("home")
    s.set_scale(30)
    s.set_scale(0)
    s.show_devtools()
    s.dismiss()
    with pytest.raises(BlingError, match="no Launch apps menu"):
        s.launch_app("Terminal")
    assert s.set_device("iPhone 15") == "ready"
    assert s._menu_lists()["device"] == "iPhone 15"


def test_mac_slug_with_a_version(s):
    assert s.set_os("macOS 15") == "ready"
    assert s.is_mac and "/browse/mac15/" in s.page.url
    assert "Terminal" in s.apps()
