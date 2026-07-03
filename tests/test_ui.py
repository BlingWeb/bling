"""Unit tests for the rich presentation layer — no live session, no real terminal.

Guards the two things most likely to regress: the prompt must degrade to plain text when
there's no terminal, and every bit of chrome must encode on a legacy Windows code page.

pytest bling/tests/test_ui.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling.ui as ui


def _cp1252_console() -> Console:
    """A Console that writes through a legacy cp1252 stream — raises on any char it can't encode."""
    return Console(file=io.TextIOWrapper(io.BytesIO(), encoding="cp1252"), width=80)


class _HAR:
    url = "demo.browserling.com"
    creator = "Firefox"
    # Deliberately includes a malformed entry to prove har_summary skips it rather than crash.
    entries = [{"response": {"status": 200}}, {"response": {"status": 404}}, {"bad": "shape"}]

    def __len__(self) -> int:
        return 3


def test_styled_prompt_is_plain_without_a_terminal(monkeypatch):
    monkeypatch.setattr(ui, "out", _cp1252_console())  # a non-terminal console
    assert ui.styled_prompt(recording=True) == "bling> "
    assert ui.styled_prompt(recording=False) == "bling> "


def test_chrome_is_cp1252_safe(monkeypatch):
    monkeypatch.setattr(ui, "err", _cp1252_console())
    monkeypatch.setattr(ui, "out", _cp1252_console())
    # None of these may raise UnicodeEncodeError on a legacy code page.
    ui.banner("0.1.0", "rec.bling")
    ui.command_help([("open", "open a session"), ("type_env", "type an env var value")])
    ui.success("recording -> rec.bling")
    ui.info("setting mobile proxy ...")
    ui.error_panel("no session yet - run `open <url>` first")
    ui.har_summary(_HAR())


def test_har_summary_tolerates_malformed_entries(monkeypatch):
    monkeypatch.setattr(ui, "out", _cp1252_console())
    ui.har_summary(_HAR())  # the entry with no response/status must be skipped, not crash
