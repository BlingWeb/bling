"""set_proxy against a stand-in for Browserling's proxy panel, in headless Chromium.

The stand-in copies the structure and the behaviour inspected live on 2026-09-25:
  * the window's ``data-screen`` / ``data-connected`` attributes, and screens hidden with
    ``visibility:hidden`` (which ``offsetParent`` does not notice);
  * "Connect" shows "Connecting..." with both buttons disabled until the proxy is up;
  * an active screen swaps "Connect" for "Set Location" ("New Identity" on Tor) and "Cancel"
    for "Disconnect";
  * "Set Location" and "New Identity" leave the buttons enabled and change the exit about a
    second later, and a Disconnect sent before then is undone when the new exit arrives.

    pytest tests/test_proxy_panel.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bling.errors import BlingError
from bling.session import Session

playwright = pytest.importorskip("playwright.sync_api")

_KINDS = {
    "dc": ["🇨🇦 Canada", "🇩🇪 Germany"],
    "res": ["🇺🇸 United States (East)", "🇩🇪 Germany", "🇮🇳 India"],
    "mobile": ["🇺🇸 United States", "🇨🇦 Canada"],
    "tor": ["🌎 Any country (3.1K exit nodes)", "🇨🇦 Canada (55 exit nodes)"],
}


def _screen(slug: str, options: list[str]) -> str:
    opts = "".join(f'<option value="{i}">{o}</option>' for i, o in enumerate(options))
    active = "New Identity" if slug == "tor" else "Set Location"
    return f"""
    <div class="screen screen-{slug}">
      <select id="proxy-sel-{slug}" class="sel">{opts}</select>
      <div class="buttons buttons-default">
        <button class="secondary do-cancel">Cancel</button>
        <button class="primary do-connect">Connect</button></div>
      <div class="buttons buttons-active">
        <button class="secondary do-cancel danger">Disconnect</button>
        <button class="primary do-connect">{active}</button></div>
    </div>"""


_CSS = "\n".join(
    f'.proxy-settings[data-screen="{s}"] .screen-{s} {{ visibility: visible; }}'
    for s in ["none", *_KINDS]
)

_SCRIPT = """
  const w = document.querySelector('.proxy-settings');
  const LABEL = {dc: 'Datacenter IP', res: 'Residential IP', mobile: 'Mobile IP',
                 tor: 'Tor Exit Node'};
  window.connectMs = 300;   // how long "Connecting..." lasts; -1 never finishes
  window.swapMs = 1000;     // how late Set Location / New Identity change the exit
  window.clicks = [];
  window.live = null;       // the kind that is connected, as the server sees it
  window.refuse = 0;        // how many applies Browserling refuses with its error popup
  const pop = document.querySelector('#group-popups .popup');
  pop.querySelector('.primary').onclick = () => { pop.dataset.isVisible = 'false'; };
  const show = (slug, exit) => {
    window.live = slug;
    w.dataset.screen = slug;
    w.dataset.connected = '2';
    document.querySelector('.proxy-kind').textContent = LABEL[slug];
    document.querySelector('.proxy-ip').textContent = exit;
  };
  document.querySelector('.proxy-and-vpn').onclick = () => { w.dataset.isVisible = 'true'; };
  document.querySelectorAll('.use-btn').forEach(c => c.onclick = () => {
    w.dataset.screen = c.className.split('use-')[2];
  });
  document.querySelectorAll('.do-cancel').forEach(b => b.onclick = () => {
    window.clicks.push(b.textContent);
    if (w.dataset.connected) {
      window.live = null;
      document.querySelector('.proxy-kind').textContent = 'Not using proxy';
      document.querySelector('.proxy-ip').textContent = 'direct';
    }
    delete w.dataset.connected; w.dataset.screen = 'none';
  });
  document.querySelectorAll('.do-connect').forEach(b => b.onclick = () => {
    window.clicks.push(b.textContent);
    const row = b.closest('.screen');
    const slug = row.className.split('screen-')[1];
    const sel = row.querySelector('select');
    const exit = sel.options[sel.selectedIndex].textContent;
    if (window.refuse > 0) {
      // Browserling's error popup, seen live on a residential Set Location.
      window.refuse--;
      setTimeout(() => { pop.dataset.isVisible = 'true'; }, 100);
      return;
    }
    if (w.dataset.connected) {
      // Set Location / New Identity: buttons stay enabled, the exit changes late, and it
      // lands even if Disconnect was pressed in between (seen live on Tor).
      setTimeout(() => show(slug, exit), window.swapMs);
      return;
    }
    const both = [...row.querySelectorAll('button')];
    const label = b.textContent;
    both.forEach(x => x.disabled = true); b.textContent = 'Connecting...';
    if (window.connectMs < 0) return;
    setTimeout(() => {
      both.forEach(x => x.disabled = false); b.textContent = label;
      show(slug, exit);
    }, window.connectMs);
  });
"""

_PAGE = f"""<!doctype html><html><head><style>
  .proxy-settings {{ display: none; }}
  .proxy-settings[data-is-visible="true"] {{ display: block; }}
  .screen {{ visibility: hidden; position: absolute; }}
  {_CSS}
  .proxy-settings[data-connected] .buttons-default {{ display: none; }}
  .proxy-settings:not([data-connected]) .buttons-active {{ display: none; }}
  /* Like the real one: a full-page overlay that takes every click until closed. */
  .popup {{ position: fixed; inset: 0; background: rgba(0,0,0,.3); z-index: 999; }}
  .popup[data-is-visible="false"] {{ display: none; }}
</style></head><body>
<div class="button-item proxy-and-vpn">Proxy</div>
<div class="proxy-settings interactive-window" data-screen="none">
  <div class="proxy-status"><span class="proxy-kind">Not using proxy</span>
    <span class="proxy-ip">direct</span></div>
  <div class="screen screen-none">
    {"".join(f'<div class="use-btn use-{s}">{s}</div>' for s in _KINDS)}
  </div>
  {"".join(_screen(s, o) for s, o in _KINDS.items())}
</div>
<div id="group-popups"><div class="popup generic" data-is-visible="false">
  <h1>Couldn't set proxy</h1><p>Connecting to proxy failed, please try again.</p>
  <button class="secondary"></button><button class="primary">OK</button></div></div>
<script>{_SCRIPT}</script></body></html>"""


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001 - no bundled Chromium on this machine
            pytest.skip(f"no headless Chromium: {e}")
        yield b
        b.close()


@pytest.fixture
def session(browser, monkeypatch):
    import bling.session as bs

    monkeypatch.setattr(bs, "_PROXY_SETTLE_MS", 200)  # the stand-in never snaps back late
    page = browser.new_page()
    page.set_content(_PAGE)
    s = Session()
    s.page = page
    yield s
    page.close()


def _status(s):
    """``"<connected kind> <exit>"`` as the stand-in server sees it, e.g. ``"res 🇩🇪 Germany"``."""
    return s.page.evaluate("window.live + ' ' + document.querySelector('.proxy-ip').textContent")


def test_connects_from_the_chooser(session):
    session.set_proxy("residential", country="germany")
    assert _status(session) == "res 🇩🇪 Germany"


def test_switching_kind_while_connected_disconnects_first(session):
    # The 2026-09-25 failure: res connected, so the panel reopened on the res screen and
    # #proxy-sel-mobile stayed hidden until select_option timed out.
    session.set_proxy("residential", country="germany")
    session.page.evaluate("document.querySelector('.proxy-settings').dataset.isVisible = ''")
    session.set_proxy("mobile", country="canada")
    assert _status(session) == "mobile 🇨🇦 Canada"
    assert "Disconnect" in session.page.evaluate("window.clicks")


def test_same_kind_again_uses_set_location(session):
    session.set_proxy("residential", country="germany")
    session.set_proxy("residential", country="india")
    assert _status(session) == "res 🇮🇳 India"
    assert session.page.evaluate("window.clicks")[-1] == "Set Location"


def test_tor_again_uses_new_identity(session):
    # Used to raise "couldn't find the connect button on the tor proxy screen".
    session.set_proxy("tor")
    session.set_proxy("tor", country="canada")
    assert session.page.evaluate("window.clicks")[-1] == "New Identity"


def test_waits_out_a_connect_in_flight(session):
    session.page.evaluate("window.connectMs = 1500")
    session.page.locator(".proxy-and-vpn").click()
    session.page.locator(".use-res").click()
    session.page.locator(".screen-res .buttons-default .do-connect").click()  # now "Connecting..."
    session.set_proxy("mobile", country="canada")
    assert _status(session) == "mobile 🇨🇦 Canada"


def test_unoffered_country_names_the_alternatives(session):
    with pytest.raises(BlingError) as e:
        session.set_proxy("residential", country="canada")
    msg = str(e.value)
    assert "Germany" in msg and "India" in msg  # what residential offers
    assert "datacenter" in msg and "mobile" in msg and "tor" in msg  # who has Canada


def test_unknown_kind(session):
    with pytest.raises(BlingError, match="unknown proxy kind"):
        session.set_proxy("satellite")


def test_timeout_carries_panel_state_and_call_log(session, monkeypatch):
    import bling.session as bs

    monkeypatch.setattr(bs, "_PROXY_CONNECT_MS", 800)
    session.page.evaluate("window.connectMs = -1")
    with pytest.raises(BlingError) as e:
        session.set_proxy("residential", country="germany")
    msg = str(e.value)
    assert "Connecting..." in msg  # the panel state at failure
    assert "Timeout" in msg  # Playwright's own message, with its call log


def test_unoffered_country_leaves_the_current_proxy_alone(session):
    session.set_proxy("mobile", country="canada")
    with pytest.raises(BlingError):
        session.set_proxy("residential", country="canada")
    assert _status(session) == "mobile 🇨🇦 Canada"
    assert "Disconnect" not in session.page.evaluate("window.clicks")


def test_switch_after_new_identity_is_not_undone(session):
    # Seen live 2026-09-25: New Identity left the buttons enabled and changed the exit ~3s
    # later, so a Disconnect sent straight after it was undone and datacenter never connected.
    session.set_proxy("tor")
    session.set_proxy("tor", country="canada")
    assert _status(session) == "tor 🇨🇦 Canada (55 exit nodes)"
    session.set_proxy("datacenter", country="germany")
    assert _status(session) == "dc 🇩🇪 Germany"


def test_same_exit_again_is_not_an_error(session, monkeypatch):
    import bling.session as bs

    monkeypatch.setattr(bs, "_PROXY_REEXIT_MS", 300)
    session.page.evaluate("window.swapMs = 50")
    session.set_proxy("residential", country="germany")
    session.set_proxy("residential", country="germany")  # status line never changes
    assert _status(session) == "res 🇩🇪 Germany"


def _popup_open(s):
    return s.page.evaluate("document.querySelector('#group-popups .popup').dataset.isVisible")


def test_one_refusal_is_retried(session):
    session.page.evaluate("window.refuse = 1")
    session.set_proxy("residential", country="germany")
    assert _status(session) == "res 🇩🇪 Germany"
    assert _popup_open(session) == "false"


def test_two_refusals_report_browserlings_words_and_keep_the_old_proxy(session):
    # Seen live 2026-09-25: a residential Set Location to India got "Couldn't set proxy".
    # The popup has only an OK button, the old closer ignored it, and it then blocked
    # every later click until each one timed out.
    session.set_proxy("residential", country="germany")
    session.page.evaluate("window.refuse = 2")
    with pytest.raises(BlingError) as e:
        session.set_proxy("residential", country="india")
    msg = str(e.value)
    assert "Couldn't set proxy" in msg and "please try again" in msg
    assert "Residential IP" in msg  # what the session kept
    assert _popup_open(session) == "false"  # closed, so the next click is not blocked
    session.set_proxy("mobile", country="canada")
    assert _status(session) == "mobile 🇨🇦 Canada"


def test_a_leftover_popup_is_closed_first(session):
    session.page.evaluate(
        "document.querySelector('#group-popups .popup').dataset.isVisible = 'true'"
    )
    session.set_proxy("residential", country="germany")
    assert _status(session) == "res 🇩🇪 Germany"
