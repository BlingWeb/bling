# bling

Drive a [Browserling](https://www.browserling.com) sandbox from Python — open a disposable
remote browser, run shell commands on its VM, move files in and out, and **capture a URL's
HAR from a real browser** (no CDP, no automation flags). Built to be obvious for humans and
for AI agents. See [`CODING_STANDARD.md`](CODING_STANDARD.md).

## Install

```bash
pip install -e .
playwright install chrome      # bling drives channel="chrome" — real Google Chrome, not Chromium
```
(Google Chrome must be installed; `playwright install chrome` provisions the Chrome channel.)

Put your Browserling credentials in `keys.env` (or `.env`) up the tree:

```
BROWSERLING_EMAIL=you@example.com
BROWSERLING_PASSWORD=...
```

## Log in once

Browserling's login is reCAPTCHA-gated and is **never** auto-solved. Do it once; the cookie
persists in a Chrome profile so later runs are unattended:

```bash
bling login          # solve the CAPTCHA in the window that opens
```

## The one-liner

```python
import bling

har = bling.har("demo.browserling.com", out="demo.har")
print(len(har), har.creator)        # 7 Firefox
print(har.urls())
```

```bash
bling har demo.browserling.com --out demo.har
```

The URL loads in a **real Firefox** on Browserling's disposable VM (isolated from your
machine), and its built-in netmonitor HAR auto-export is egressed back to you.

Runs **headless by default**. To watch the browser while it works, pass `live=True`
(`bling.har(url, live=True)`) or `--live` on the CLI.

For an attended session — e.g. signing into a site by hand inside the VM — keep it open:

```bash
bling open https://app.example.com/login --keep-open
# close the browser window (or Ctrl-C here) to end the VM
```

`--keep-open` implies `--live`, skips the closing screenshot, and parks until the browser
window closes (or you Ctrl-C the terminal). No TTY required.

## Power use

```python
import bling

with bling.Session() as s:           # always a context manager
    s.require_login()
    s.open("example.com", browser="firefox")
    print(s.run("whoami"))           # shell on the VM (admin)
    s.upload("tool.py")              # push a file into the VM
    s.run("python C:/Users/user/Downloads/tool.py")
    s.download("result.json", "result.json")
    s.set_proxy("datacenter", country="germany")   # matched case-insensitively
    s.screenshot("shot.png")
```

To see a page the way another country does — for spotting **geo-cloaked** malware that serves
different content by region — capture it through a proxy and diff the results:

```bash
bling har evil.example --proxy datacenter --country germany --out de.har
bling urls de.har        # vs. another country's — the differing requests are the cloaking
```

Or drive a session by hand and save it as a replayable script with the **interactive shell**:

```bash
bling shell --record login.bling    # type verbs, watch them hit a live VM, record
bling play  login.bling             # replay unattended (no prompt, no TTY)
```

Secrets stay out of recordings — `type_env APP_PASSWORD` types the env var's value but
records only its name. Full command reference in [`docs/SHELL.md`](docs/SHELL.md).

## Docs & examples

- **[`docs/API.md`](docs/API.md)** — the full public API on one page.
- **[`examples/`](examples/README.md)** — runnable walkthroughs: [`quickstart.py`](examples/quickstart.py)
  (the one-liner + a power-user session) and [`geo_cloaking.py`](examples/geo_cloaking.py)
  (capture a URL across countries and diff the cloaking).
- Docstrings are reference docs too: `help(bling.har)`, `help(bling.Session)`, `help(bling.HAR)`.

## Errors

All errors subclass `bling.BlingError`; catch a specific one to branch:
`NotLoggedIn`, `SessionBlocked`, `NotReady`, `Timeout`, `EgressError`. Messages tell you how
to fix them (e.g. *"Not logged in — run: bling login"*).

## Security

A HAR records **everything** a page did on the wire — request cookies, `Authorization` headers,
session tokens, and any credentials submitted in a form — all in plaintext. Treat a HAR of a
logged-in session as sensitive as the login itself: don't commit it, and scrub it before
sharing. (For analysing hostile pages you never log in, so this rarely bites — but know it.)
The capture runs in a disposable VM isolated from your machine, so the page's code never touches
your host.

## How it works

A Browserling session is two layers, and the API hides the seam:

- **Outer page** (`browserling.com`) — real DOM, driven with Playwright (the control panel).
- **Inner remote browser** — pixels in a canvas; synthetic keyboard/mouse forward into the
  VM, so shell/keystroke ops run "blind" (Win+R, etc.). Files move via Browserling's curl
  file-transfer (HTTP PUT in, GET out).
