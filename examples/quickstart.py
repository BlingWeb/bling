"""bling quickstart — the one-liner and the power-user session.

Run from the bling project dir after `bling login`:
    python examples/quickstart.py
(Installed via `pip install -e .`? Then the sys.path shim below is a no-op.)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling


def one_liner():
    """The 90% case: a URL in, a HAR out (real Firefox, no CDP)."""
    har = bling.har("demo.browserling.com", out="demo.har")
    print(har)  # <HAR 'demo.browserling.com' 7 entries, creator Firefox>
    print("creator:", har.creator)
    print("urls:", *har.urls(), sep="\n  ")


def power_session():
    """Do several things in one session: shell, file round-trip, screenshot."""
    with bling.Session() as s:
        s.require_login()
        s.open("example.com", browser="firefox")
        print("user:", s.run("whoami"))
        print("python:", s.run("python --version"))
        s.upload_text("hello from bling", "note.txt")
        out = s.download("note.txt", "note_roundtrip.txt")
        print("round-trip:", out.read_text())
        s.screenshot("session.png")


if __name__ == "__main__":
    one_liner()
    # power_session()   # uncomment to try the Session API
