"""Geo-cloaking detection: capture a URL as served to different countries, then diff them.

Some phishing and malware pages serve different content by visitor country — a harmless page
in one region, a hostile one in another — to slip past scanners. This opens a session per
country, routes it through that country's proxy exit, and captures both a HAR (every network
request the page made) and a screenshot (what the victim actually sees). Then it prints which
requests differ — the cloaked behaviour.

Run after `bling login`:
    python examples/geo_cloaking.py https://suspicious.example
    python examples/geo_cloaking.py                 # defaults to a Browserling security-lab demo
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling

URL = sys.argv[1] if len(sys.argv) > 1 else "https://browser-security-lab.com/rc-cars-shop/"
COUNTRIES = ["germany", "united states"]  # datacenter exits to compare (case-insensitive)
OUT = Path("cloaking-run")


def capture_from(country: str) -> bling.HAR:
    """Route a fresh session through `country`, then capture the URL's HAR and a screenshot."""
    slug = country.replace(" ", "_")
    with bling.Session(headless=True) as s:
        s.require_login()
        s.open("example.com", browser="firefox")  # any starting page; capture loads the target
        s.set_proxy("datacenter", country=country)
        s.wait_ready()
        har = bling.capture_here(s, URL, timeout=75)  # datacenter exits can be slow
        s.screenshot(OUT / f"{slug}.png")  # full remote-browser screenshot: what the victim sees
    har.save(OUT / f"{slug}.har")
    return har


OUT.mkdir(exist_ok=True)
captures = {c: capture_from(c) for c in COUNTRIES}
for country, har in captures.items():
    slug = country.replace(" ", "_")
    print(f"{country:>16}: {len(har):>3} requests  ->  {OUT}/{slug}.har + {slug}.png")

a, b = COUNTRIES[0], COUNTRIES[1]
only_a = sorted(set(captures[a].urls()) - set(captures[b].urls()))
only_b = sorted(set(captures[b].urls()) - set(captures[a].urls()))
print(f"\nRequests only in {a}:")
print(*(f"  {u}" for u in only_a), sep="\n") if only_a else print("  (none)")
print(f"Requests only in {b}:")
print(*(f"  {u}" for u in only_b), sep="\n") if only_b else print("  (none)")
print("\nIf those lists differ, the page is serving different content by country.")
