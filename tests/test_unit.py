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
