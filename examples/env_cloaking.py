"""Environment cloaking: screenshot the same URL across browsers and OSes to catch UA sniffing.

Some pages serve a different payload depending on the *browser* or *operating system* asking —
hiding from modern Chrome but showing a fake "update Flash" lure to what looks like an old IE,
or serving a mobile-only page to Android. You catch that by opening the same URL in each
environment and comparing what renders. This opens a matrix of (OS, browser) combinations,
screenshots each, and leaves you a folder of PNGs to compare by eye.

This is the *visual* sibling of geo_cloaking.py. That one diffs the network requests (HAR) across
countries; this one diffs the rendered page across browsers/OSes. They're separate axes because
bling's HAR capture is always Firefox-on-Windows — so browser/OS cloaking is a screenshot game,
not a HAR diff.

Run after `bling login`:
    python examples/env_cloaking.py https://suspicious.example
    python examples/env_cloaking.py                 # defaults to a Browserling security-lab demo
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling

URL = sys.argv[1] if len(sys.argv) > 1 else "https://browser-security-lab.com/rc-cars-shop/"
OUT = Path("env-cloaking-run")

# (os, browser) slugs — see bling.config.OS_SLUGS / BROWSER_SLUGS for the full picker.
# A modern browser, a legacy one that malware loves to target, and a mobile OS.
MATRIX = [
    ("win10", "chrome138"),
    ("win10", "ie11"),
    ("android", "chrome138"),
]


def shoot(os_slug: str, browser: str) -> Path:
    """Open URL in one (os, browser), then screenshot what the page actually renders there."""
    dest = OUT / f"{os_slug}-{browser}.png"
    with bling.Session(headless=True) as s:
        s.require_login()
        s.open(URL, os=os_slug, browser=browser)
        s.screenshot(dest)
    return dest


OUT.mkdir(exist_ok=True)
print(f"Opening {URL} across {len(MATRIX)} environments ...\n")
for os_slug, browser in MATRIX:
    try:
        path = shoot(os_slug, browser)
        print(f"  {os_slug:>8}/{browser:<10} -> {path}")
    except bling.BlingError as e:
        # An OS/browser combo the plan doesn't offer just gets skipped, not fatal.
        print(f"  {os_slug:>8}/{browser:<10} -> skipped ({e})")

print(
    f"\nCompare the PNGs in {OUT}/. If one environment shows a different page — a login lure, a "
    "fake update prompt, a mobile-only screen — that's browser/OS cloaking."
)
