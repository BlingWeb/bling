"""Unit tests for bling — no live Browserling session needed.

pytest bling/tests/test_unit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

from bling.cli import _host
from bling.errors import BlingError
from bling.har import HAR, _render_scripts
from bling.session import _parse_curl_token


# --- _parse_curl_token (download + upload 'see how' formats) -------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("curl -O https://s8.browserling.com/abc123/file.txt", ("s8.browserling.com", "abc123")),
        (
            "curl https://s12.browserling.com/7bbbd3f9/ -T file.txt",
            ("s12.browserling.com", "7bbbd3f9"),
        ),
        (
            "  curl -O https://s140.browserling.com/DEADbeef00/x.har ",
            ("s140.browserling.com", "DEADbeef00"),
        ),
        # Seen live 2026-09-17: transfer servers are not always sNN.
        (
            "curl https://b8.browserling.com/2abc9xyz01/ -T file.txt",
            ("b8.browserling.com", "2abc9xyz01"),
        ),
        ("curl -O https://www.browserling.com/abc123/file.txt", None),
        ("no url here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_curl_token(text, expected):
    assert _parse_curl_token(text) == expected


# --- _render_scripts (would have caught C1 stale sentinels / placeholder bugs) ---
def test_render_scripts_no_unrendered_placeholders():
    s = _render_scripts("abcd1234")
    assert "__FF__" not in s["launch.bat"]
    assert "__DONE__" not in s["launch.bat"]


def test_render_scripts_tagged_names():
    s = _render_scripts("abcd1234")
    assert s["launch_done"] == "launch_abcd1234.done"
    assert "launch_abcd1234.done" in s["launch.bat"]


def test_render_scripts_unique_per_tag():
    a, b = _render_scripts("aaaa"), _render_scripts("bbbb")
    assert a["launch_done"] != b["launch_done"]


# --- HAR ----------------------------------------------------------------
@pytest.fixture
def sample_har():
    data = {
        "log": {
            "creator": {"name": "Firefox"},
            "entries": [
                {"request": {"url": "https://demo.browserling.com/"}, "response": {"status": 200}},
                {"request": {"url": "https://x.example/a.js"}, "response": {"status": 200}},
            ],
        }
    }
    return HAR(data, "demo.browserling.com")


def test_har_len_and_creator(sample_har):
    assert len(sample_har) == 2
    assert sample_har.creator == "Firefox"


def test_har_urls(sample_har):
    assert sample_har.urls() == ["https://demo.browserling.com/", "https://x.example/a.js"]


def test_har_repr(sample_har):
    assert repr(sample_har) == "<HAR 'demo.browserling.com' 2 entries, creator Firefox>"


def test_har_save_roundtrip(sample_har, tmp_path):
    out = sample_har.save(tmp_path / "x.har")
    assert out.exists()
    assert json.loads(out.read_text())["log"]["creator"]["name"] == "Firefox"


def test_har_load_roundtrip(sample_har, tmp_path):
    path = sample_har.save(tmp_path / "x.har")
    h = HAR.load(path)
    assert h.urls() == sample_har.urls()
    assert h.url == "https://demo.browserling.com/"  # recovered from the first entry


def test_har_load_rejects_non_har_json(tmp_path):
    p = tmp_path / "not.har"
    p.write_text('{"hello": "world"}')
    with pytest.raises(BlingError, match="not a HAR"):
        HAR.load(p)


def test_har_load_rejects_missing_file(tmp_path):
    with pytest.raises(BlingError, match="cannot read"):
        HAR.load(tmp_path / "absent.har")


def test_cli_urls_prints_one_per_line(sample_har, tmp_path, capsys):
    from bling.cli import main

    path = sample_har.save(tmp_path / "x.har")
    assert main(["urls", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["https://demo.browserling.com/", "https://x.example/a.js"]


# --- cli._host ----------------------------------------------------------
@pytest.mark.parametrize(
    "url, host",
    [
        ("demo.browserling.com", "demo.browserling.com"),
        ("https://evil.example/path?q=1", "evil.example"),
        ("http://sub.evil.example:8080/x", "sub.evil.example"),
        ("ftp://host.tld/", "host.tld"),
    ],
)
def test_host(url, host):
    assert _host(url) == host


# --- run_script on a reused VM (seen 2026-09-25: a stale sentinel returned at once) ---
class _FakePage:
    def __init__(self, log):
        self.log = log
        self.keyboard = self
        self.mouse = self

    def press(self, key):
        self.log.append(("press", key))

    def type(self, text, delay=0):
        self.log.append(("type", text))

    def click(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


def _scripted_session(monkeypatch, exists_before, poll_status=200):
    import bling.session as bs
    from bling.session import Session

    log = []
    s = Session()
    s.page = _FakePage(log)
    s._dl_token = ("s8.browserling.com", "tok")
    checks = iter(exists_before)
    monkeypatch.setattr(s, "_vm_file_exists", lambda name: next(checks))
    monkeypatch.setattr(s, "run", lambda cmd, **k: log.append(("run", cmd)) or "")
    monkeypatch.setattr(bs.requests, "get", lambda *a, **k: _Resp(poll_status, b"OK"))
    return s, log


def test_run_script_deletes_a_stale_sentinel_before_launch(monkeypatch):
    s, log = _scripted_session(monkeypatch, exists_before=[True, False])
    s.run_script("launch.bat", sentinel="done.flag", timeout=5, poll=0)
    runs = [i for i, e in enumerate(log) if e[0] == "run"]
    launch = next(i for i, e in enumerate(log) if e[0] == "type" and "launch.bat" in e[1])
    assert runs and runs[0] < launch  # deleted first, then launched
    assert "done.flag" in log[runs[0]][1]


def test_run_script_skips_the_delete_when_there_is_no_sentinel(monkeypatch):
    s, log = _scripted_session(monkeypatch, exists_before=[False])
    s.run_script("launch.bat", sentinel="done.flag", timeout=5, poll=0)
    assert not [e for e in log if e[0] == "run"]


def test_run_script_refuses_a_sentinel_that_will_not_delete(monkeypatch):
    s, log = _scripted_session(monkeypatch, exists_before=[True, True])
    with pytest.raises(BlingError, match="could not delete done.flag"):
        s.run_script("launch.bat", sentinel="done.flag", timeout=5, poll=0)
    assert not [e for e in log if e[0] == "type"]  # never launched


@pytest.mark.parametrize("bad", ["", r"..\x", "a b", "x&del", 'q"', "dir/f", "*.har"])
def test_vm_names_must_be_plain(bad):
    from bling.session import _check_vm_name

    with pytest.raises(BlingError, match="plain filename"):
        _check_vm_name(bad)


def test_remove_rejects_paths_before_touching_the_vm(monkeypatch):
    s, log = _scripted_session(monkeypatch, exists_before=[])
    with pytest.raises(BlingError):
        s.remove("ok.txt", r"..\evil")
    assert log == []
