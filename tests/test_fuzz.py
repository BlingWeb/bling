"""Property-based fuzz tests for bling's pure surfaces — no live session.

    pytest bling/tests/test_fuzz.py

Invariant under fuzzing: these never crash on arbitrary/hostile input, and the safety guards
(no unrendered placeholders; run() rejects shell-breaking commands) always hold.
"""

from __future__ import annotations

import string
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bling.cli import _host
from bling.errors import BlingError
from bling.har import _render_scripts
from bling.session import Session, _parse_curl_token


@given(st.text(max_size=300))
def test_parse_curl_token_never_crashes(text):
    r = _parse_curl_token(text)
    assert r is None or (
        isinstance(r, tuple) and len(r) == 2 and all(isinstance(x, str) for x in r)
    )


@given(
    st.from_regex(r"s[0-9]{1,3}", fullmatch=True),
    st.from_regex(r"[A-Za-z0-9]{1,40}", fullmatch=True),
)
def test_parse_curl_token_roundtrip(server_n, token):
    text = f"curl -O https://{server_n}.browserling.com/{token}/file.har"
    assert _parse_curl_token(text) == (f"{server_n}.browserling.com", token)


@given(st.text(alphabet="0123456789abcdef", min_size=1, max_size=8))  # tag is a uuid hex slice
def test_render_scripts_invariants(tag):
    s = _render_scripts(tag)
    assert "__FF__" not in s["launch.bat"]
    assert "__DONE__" not in s["launch.bat"]
    assert s["launch_done"] == f"launch_{tag}.done"
    assert s["launch_done"] in s["launch.bat"]


@given(st.text(max_size=300))
def test_host_never_crashes(url):
    h = _host(url)
    assert isinstance(h, str) and h


@given(st.text(max_size=200).map(lambda s: s + '"'))  # always contains a double-quote
def test_run_rejects_double_quotes(cmd):
    with pytest.raises(BlingError):
        Session().run(cmd)


@given(st.text(alphabet=string.ascii_lowercase, min_size=300, max_size=400))  # no quotes, too long
def test_run_rejects_overlong_command(cmd):
    with pytest.raises(BlingError):
        Session().run(cmd)
