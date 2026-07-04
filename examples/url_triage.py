"""URL triage: from a captured HAR, list every third-party domain the page talked to.

A phishing or malware page usually loads its own HTML from one host but then quietly reaches
out to others — a tracker, a redirector, a credential-exfil endpoint. Those third-party hosts
are the interesting part of a capture. This reads a `.har` (from `bling har`, capture mode, or
any browser's DevTools), works out the page's own domain, and prints who *else* it contacted,
busiest first — so an exfil endpoint that got one POST still shows up next to the noisy CDNs.

No Browserling session needed — it's pure local analysis of a file you already captured:
    python examples/url_triage.py captures/rc-cars-germany.har
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling


def registrable(host: str) -> str:
    """The last two labels of a hostname — a cheap stand-in for the registrable domain.

    Good enough to group `www.evil.com` and `api.evil.com` together as `evil.com`. It's a
    heuristic: it mislabels multi-part TLDs like `co.uk` (treating `foo.co.uk` as `co.uk`),
    which is fine for eyeballing a triage list but don't build blocking on it.
    """
    labels = host.lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python examples/url_triage.py <file.har>")
        return 2
    har = bling.HAR.load(argv[0])

    hosts = [urlparse(u).hostname or "" for u in har.urls()]
    hosts = [h for h in hosts if h]  # drop about:blank / data: entries with no host
    if not hosts:
        print(f"{har.url}: no hosts in this HAR ({len(har)} entries)")
        return 0

    own = registrable(hosts[0])  # the first request is the page itself
    third_party = Counter(h for h in hosts if registrable(h) != own)

    print(f"page:        {har.url}")
    print(f"own domain:  {own}")
    print(f"requests:    {len(har)} total, {len(third_party)} to third-party hosts\n")
    if not third_party:
        print("no third-party hosts — every request stayed on the page's own domain.")
        return 0

    print("third-party hosts (most-contacted first):")
    width = max(len(h) for h in third_party)
    for host, n in third_party.most_common():
        print(f"  {host:<{width}}  {n:>3} request{'s' if n != 1 else ''}")
    print("\nAnything here you don't recognise is worth a closer look - that's the exfil surface.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
