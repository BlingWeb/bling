# bling

**Drive a [Browserling](https://www.browserling.com) sandbox from Python** — automate a
disposable remote browser to triage phishing, malware, and other hostile web pages.

Browserling is an online browser sandbox: a real browser running on a throwaway VM in the
cloud, walled off from your own machine. Security teams use it to open suspicious links, triage
phishing emails, and do light malware and web research without a hostile page ever running code
on their host. bling opens one of those disposable sessions and drives it for you — from Python
or the `bling` command line — instead of clicking through it by hand.

That automation matters because hostile pages fight back. A malware page will bounce a visitor
through a chain of redirects and **cloak** itself based on who's asking — serving the real
payload only to the right country, operating system, or a residential (rather than datacenter)
IP, and a harmless decoy to everyone else. bling lets you switch proxy exits (datacenter,
residential, mobile, Tor) and swap the OS or browser between runs, so you can see what the page
shows each kind of visitor.

To capture what a page actually did, bling produces a **HAR file** (HTTP Archive) — a JSON log
of every network request the page made: each redirect, script, and API call, with headers and
timing. It's recorded by a real Firefox inside the VM, with no CDP or automation flags a page
could detect, so the log is faithful and you analyse it offline.

> **Scope:** HAR capture and the VM shell / file-transfer features run in a **Firefox-on-Windows**
> VM — bling drives Firefox's own built-in network monitor, and the shell uses Windows
> conventions (Win+R, the `Downloads` folder). You can still *open* a page in other browsers or
> OSes to compare what renders — see the [browser/OS cloaking example](examples/env_cloaking.py) —
> but the network capture itself is Firefox-on-Windows.

Built to be obvious for humans and for AI agents. See [`CODING_STANDARD.md`](CODING_STANDARD.md).

## Install

Python 3.10+.

```bash
git clone https://github.com/BlingWeb/bling.git
cd bling
pip install -e .
playwright install chrome      # bling drives channel="chrome" — real Google Chrome, not Chromium
```
(Google Chrome must be installed; `playwright install chrome` provisions the Chrome channel.
If it warns that chrome is already installed, you're done. Ignore its suggestion to re-run
with `--force`, which starts by uninstalling your system Chrome.)

bling uses **two browsers for two different jobs**. A real **Chrome on your own machine** is the
driver — bling automates it with Playwright to operate Browserling and to hold your login. The
suspect page itself loads in a real **Firefox inside the VM**, which is where the HAR is captured.
That's why you install Chrome locally, yet every capture's `creator` is Firefox.

If you want `bling login` to pre-fill the sign-in form, copy the example env file and add
your Browserling account. This is optional; typing your credentials into the login window
works the same. Use your own account, not a shared one — the login ties to a local Chrome
profile, and two people on one account collide with an "another host joined" error.

```bash
cp .env.example .env      # then edit .env
```

```
BROWSERLING_EMAIL=you@example.com
BROWSERLING_PASSWORD=...
```

(bling reads `keys.env` or `.env`, searching from the current directory upward.)

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
print(len(har), har.creator)        # e.g. 19 Firefox
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

- **[`docs/API.md`](docs/API.md)** — the full Python API on one page, plus the **`bling`
  command-line reference** (`bling har`, `urls`, `open`, `run`, `shell`, `play`).
- **[`docs/SHELL.md`](docs/SHELL.md)** — the interactive **`bling shell`** for driving a session
  by hand, and recording a run to a replayable `.bling` script.
- **[`examples/`](examples/README.md)** — seven runnable walkthroughs: the
  [quickstart](examples/quickstart.py), HAR [URL triage](examples/url_triage.py),
  [geo-](examples/geo_cloaking.py) and [browser/OS](examples/env_cloaking.py) cloaking,
  [running a tool in the VM](examples/run_in_vm.py), and the click-through / login-capture
  [recordings](examples/click_through.bling).
- Docstrings are reference docs too: `help(bling.har)`, `help(bling.Session)`, `help(bling.HAR)`.

## Errors

All errors subclass `bling.BlingError`; catch a specific one to branch:
`NotLoggedIn`, `SessionBlocked`, `NotReady`, `Timeout`, `EgressError`. Messages tell you how
to fix them (e.g. *"Not logged in — run: bling login"*).

## Security

**You never have to run a hostile page yourself.** It executed in the throwaway VM, isolated from
your host; what comes back is the HAR — an inert JSON log of what the page requested. The whole
workflow is to pull that log down and pick through it locally, so the page's code never runs on
your machine.

**But treat the URLs inside it as live and hostile.** The file doesn't execute on its own, yet
it's a list of the exact malware URLs the page touched. Read it *as data* — a text or JSON editor,
`bling urls`, or the [URL-triage example](examples/url_triage.py) — and **don't open a captured HAR
in a web browser, or any viewer that turns its links into clickable ones.** One click sends your
own machine straight to the live payload, outside the sandbox that was protecting you.

**And know it carries secrets, in plaintext** — request cookies, `Authorization` headers, session
tokens, and anything submitted in a form. A HAR of your *own* logged-in session is as sensitive as
the login itself: don't commit it, and scrub it before sharing. Triaging a hostile page you never
log in, so this rarely bites — but know it's there.

## How it works

A Browserling session is two layers, and the API hides the seam:

- **Outer page** (`browserling.com`) — real DOM, driven with Playwright in a **local Chrome**
  on your machine (the control panel that carries your login).
- **Inner remote browser** — the browser running inside the VM, seen only as pixels in a canvas;
  HAR capture always loads the page in the VM's **Firefox**. Synthetic keyboard/mouse forward
  into the VM, so shell/keystroke ops run "blind" (Win+R, etc.). Files move via Browserling's
  curl file-transfer (HTTP PUT in, GET out).
