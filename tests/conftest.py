"""Shared pytest config: keep the default run offline.

Tests marked ``@pytest.mark.live`` drive a real Browserling VM (they cost a session and need
a logged-in profile — CAPTCHA means CI can't create one). They are skipped by default so a
bare ``pytest`` never spends a live session. Opt in explicitly:

    BLING_LIVE=1 pytest            # run everything, including live
    pytest -m live                 # (still skipped unless BLING_LIVE=1)
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.getenv("BLING_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live Browserling test — set BLING_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
