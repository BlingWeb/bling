"""Unit tests for the bling shell — parser, recording, secrets, and playback.

All of these run with no live Browserling session (a tiny FakeSession stands in). The one
live round-trip test is gated behind the `live` marker per CODING_STANDARD.md.

pytest bling/tests/test_shell.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

from bling.shell import BlingShell, _split_opts


class FakeSession:
    """Records the Session methods the shell calls, so verbs work with no live VM."""

    def __init__(self):
        self.calls: list[tuple] = []

    def type(self, text, **kw):
        self.calls.append(("type", text))

    def key(self, combo):
        self.calls.append(("key", combo))

    def focus_vm(self):
        self.calls.append(("focus",))

    def set_proxy(self, kind, **kw):
        self.calls.append(("set_proxy", kind, kw.get("country")))

    def wait_ready(self, **kw):
        return "ready"


@pytest.fixture
def shell_with_fake():
    """A shell whose Session is already a FakeSession — verbs needing one just work."""
    sh = BlingShell()
    sh._session = FakeSession()
    return sh


# --- _split_opts --------------------------------------------------------------
@pytest.mark.parametrize(
    "tokens, flags, expected",
    [
        (["ex.com"], {"--os"}, (["ex.com"], {})),
        (["ex.com", "--os", "win11"], {"--os", "--browser"}, (["ex.com"], {"--os": "win11"})),
        (
            ["ex.com", "--os", "win11", "--browser", "firefox"],
            {"--os", "--browser"},
            (["ex.com"], {"--os": "win11", "--browser": "firefox"}),
        ),
        (["url", "--via", "remote"], {"--via"}, (["url"], {"--via": "remote"})),
        (["--os"], {"--os"}, ([], {"--os": ""})),  # dangling flag -> empty value, no crash
    ],
)
def test_split_opts(tokens, flags, expected):
    assert _split_opts(tokens, flags) == expected


# --- recording (precmd) -------------------------------------------------------
def test_precmd_records_action_verbs(tmp_path):
    sh = BlingShell()
    sh._open_recording(tmp_path / "s.bling")
    sh.precmd("open example.com")
    sh.precmd('proxy mobile "United States"')
    sh.precmd("key Tab")
    sh._close_recording()

    body = (tmp_path / "s.bling").read_text()
    assert "open example.com" in body
    assert 'proxy mobile "United States"' in body  # quoting preserved for round-trip
    assert "key Tab" in body


def test_precmd_skips_meta_verbs(tmp_path):
    sh = BlingShell()
    sh._open_recording(tmp_path / "s.bling")
    for line in ["login", "quit", "exit", "play other.bling", "record off", "help", "?"]:
        sh.precmd(line)
    sh._close_recording()

    body = tmp_path / "s.bling"
    non_comment = [ln for ln in body.read_text().splitlines() if ln and not ln.startswith("#")]
    assert non_comment == []  # nothing but the header comment


def test_precmd_noop_without_recording():
    sh = BlingShell()
    # No recording open — precmd must return the line unchanged and not raise.
    assert sh.precmd("open example.com") == "open example.com"


# --- secrets policy (type_env) ------------------------------------------------
def test_type_env_types_value_but_records_only_name(tmp_path, monkeypatch, shell_with_fake):
    monkeypatch.setenv("BLING_TEST_PW", "hunter2")
    sh = shell_with_fake
    sh._open_recording(tmp_path / "s.bling")
    sh.precmd("type_env BLING_TEST_PW")  # what the REPL records for the line itself
    sh.onecmd("type_env BLING_TEST_PW")  # what actually runs
    sh._close_recording()

    # The secret value was typed into the VM...
    assert ("type", "hunter2") in sh._session.calls
    # ...but never written to the recording; only the variable name is.
    body = (tmp_path / "s.bling").read_text()
    assert "hunter2" not in body
    assert "type_env BLING_TEST_PW" in body
    assert "secret typed from env: BLING_TEST_PW" in body


def test_type_env_unset_var_fails_loudly(shell_with_fake, monkeypatch):
    monkeypatch.delenv("BLING_NOPE", raising=False)
    sh = shell_with_fake
    sh.onecmd("type_env BLING_NOPE")
    assert sh._error is not None
    assert "not set" in str(sh._error)
    assert sh._session.calls == []  # nothing typed


# --- dispatch / error handling ------------------------------------------------
def test_verb_without_session_errors_cleanly():
    sh = BlingShell()
    sh.onecmd("proxy mobile")
    assert sh._error is not None
    assert "no session" in str(sh._error)


def test_login_refused_while_a_session_is_open(shell_with_fake):
    # login opens a headed browser on the shared profile, so it must run before `open`.
    sh = shell_with_fake  # already holds a (fake) session
    sh.onecmd("login")
    assert sh._error is not None
    assert "before opening a session" in str(sh._error)


def test_typed_eof_char_exits():
    # Windows echoes Ctrl-D as \x04 into the line instead of raising EOFError; treat it as quit.
    sh = BlingShell()
    assert sh.onecmd("\x04") is True
    assert sh._error is None  # exiting is not an error


def test_unknown_command_still_reports_error():
    sh = BlingShell()
    result = sh.onecmd("bogus --flag")
    assert result is None  # does not exit
    assert sh._error is not None
    assert "unknown command: bogus --flag" in str(sh._error)


def test_unknown_command_sets_error():
    sh = BlingShell()
    sh.onecmd("frobnicate the thing")
    assert sh._error is not None
    assert "unknown command" in str(sh._error)


def test_emptyline_is_noop():
    sh = BlingShell()
    assert sh.emptyline() is False  # cmd.Cmd default would repeat the last command


def test_type_records_literal_text(tmp_path, shell_with_fake):
    """`type` is documented as recorded-in-plaintext; confirm it types the raw arg."""
    sh = shell_with_fake
    sh.onecmd("type hello world")
    assert ("type", "hello world") in sh._session.calls


# --- har / urls (in-shell capture + local read) --------------------------------
def test_har_usage_without_args():
    sh = BlingShell()
    sh.onecmd("har")  # just prints usage; must not error or open a session
    assert sh._error is None
    assert sh._session is None


def test_har_captures_and_saves(tmp_path, monkeypatch, shell_with_fake):
    from bling.har import HAR

    fake = HAR(
        {"log": {"creator": {"name": "Firefox"}, "entries": [
            {"request": {"url": "https://evil.example/"}, "response": {"status": 200}},
        ]}},
        "evil.example",
    )
    captured = {}

    def fake_capture(session, url, **kw):
        captured["url"] = url
        return fake

    monkeypatch.setattr("bling.shell.capture_here", fake_capture)
    monkeypatch.chdir(tmp_path)
    sh = shell_with_fake
    sh.onecmd("har evil.example")
    assert sh._error is None
    assert captured["url"] == "evil.example"
    assert (tmp_path / "evil.example.har").exists()  # default name derived from the host


def test_capture_records_pages_tagged_by_proxy(tmp_path, monkeypatch, shell_with_fake):
    from bling.har import HAR

    sh = shell_with_fake

    def mk(u):
        return HAR(
            {"log": {"creator": {"name": "Firefox"},
                     "entries": [{"request": {"url": u}, "response": {"status": 200}}]}},
            u,
        )

    monkeypatch.setattr("bling.shell.arm_capture", lambda s: None)
    monkeypatch.setattr("bling.shell.capture_count", lambda s: 2)  # 2 pages before the switch
    monkeypatch.setattr(
        "bling.shell.sweep_captures",
        lambda s: [mk("http://a.example/1"), mk("http://a.example/2"), mk("http://b.example/3")],
    )

    sh.onecmd("capture start")
    assert sh._cap is not None and sh._error is None
    sh.onecmd("proxy datacenter germany")  # records a boundary at page index 2
    assert sh._error is None
    sh.onecmd(f'capture save "{tmp_path}"')
    assert sh._error is None and sh._cap is None  # save resets capture mode

    got = sorted(p.name for p in tmp_path.glob("*.har"))
    assert got == [
        "00-a.example-noproxy.har",
        "01-a.example-noproxy.har",
        "02-b.example-datacenter-germany.har",  # tagged with the proxy active when captured
    ]


def test_capture_save_without_start_errors(shell_with_fake):
    sh = shell_with_fake
    sh.onecmd("capture save .")
    assert sh._error is not None
    assert "not recording" in str(sh._error)


def test_urls_reads_har_from_disk(tmp_path, capsys):
    from bling.har import HAR

    h = HAR(
        {"log": {"creator": {"name": "Firefox"}, "entries": [
            {"request": {"url": "https://a.example/"}, "response": {"status": 200}},
            {"request": {"url": "https://b.example/x.js"}, "response": {"status": 404}},
        ]}},
        "a.example",
    )
    path = h.save(tmp_path / "x.har")
    sh = BlingShell()
    sh.onecmd(f'urls "{path}"')
    assert sh._error is None
    assert capsys.readouterr().out.splitlines() == ["https://a.example/", "https://b.example/x.js"]


# --- playback (do_play) -------------------------------------------------------
def test_playback_skips_comments_and_blanks(tmp_path, shell_with_fake):
    rec = tmp_path / "s.bling"
    rec.write_text(
        "# a comment\n"
        "\n"
        "focus\n"
        "   # indented comment\n"
        "key Tab\n"
    )
    sh = shell_with_fake
    sh.do_play(str(rec))
    assert sh._session.calls == [("focus",), ("key", "Tab")]


def test_playback_aborts_on_first_error(tmp_path, shell_with_fake):
    rec = tmp_path / "s.bling"
    rec.write_text(
        "focus\n"
        "type_env BLING_UNSET_ABORT\n"  # raises -> abort
        "key Tab\n"  # must NOT run
    )
    sh = shell_with_fake
    sh.do_play(str(rec))
    assert sh._session.calls == [("focus",)]  # stopped before the second focus/key
    assert sh._error is not None


def test_playback_does_not_record(tmp_path, shell_with_fake):
    """Playback dispatches via onecmd (bypasses precmd), so it must not write a recording."""
    rec_in = tmp_path / "in.bling"
    rec_in.write_text("focus\nkey Tab\n")
    rec_out = tmp_path / "out.bling"

    sh = shell_with_fake
    sh._open_recording(rec_out)
    sh.do_play(str(rec_in))
    sh._close_recording()

    out_lines = [ln for ln in rec_out.read_text().splitlines() if ln and not ln.startswith("#")]
    assert out_lines == []  # only the header comment; played lines weren't recorded


def test_playback_missing_file_errors(shell_with_fake):
    sh = shell_with_fake
    sh.onecmd("play does_not_exist.bling")
    assert sh._error is not None
    assert "cannot read" in str(sh._error)


# --- live round-trip (gated) --------------------------------------------------
@pytest.mark.live
def test_record_then_replay_roundtrip(tmp_path):
    """Record a tiny real session and replay it. Needs a logged-in profile (CAPTCHA)."""
    from bling.shell import run_shell

    rec = tmp_path / "rt.bling"
    rec.write_text("open example.com\nscreenshot " + str(tmp_path / "shot.png") + "\n")
    assert run_shell(play=str(rec), headless=True, exit_after_play=True) == 0
    assert (tmp_path / "shot.png").exists()
