# bling API reference

One page. Everything public. (Docstrings carry the same info: `help(bling.har)` etc.)

**See also:** [README](../README.md) for install and the quickstart · [`SHELL.md`](SHELL.md)
for the interactive `bling shell` and record/replay.

```python
import bling
```

## Top-level

### `bling.har(url, *, out=None, os="win10", timeout=45, live=False, profile=None) -> HAR`
Capture a URL's HAR from a **real Firefox** in a Browserling sandbox (no CDP/automation).
Uses the persistent login cookie. Pass `out` to also write the `.har` to disk. Runs
**headless** by default; pass `live=True` to watch the browser window. After navigating it
polls for the export until it appears and settles (`timeout` bounds the wait). The captured
browser is always Firefox; `os` selects the Windows VM.
```python
h = bling.har("demo.browserling.com", out="demo.har")          # headless
h = bling.har("demo.browserling.com", live=True, timeout=60)   # visible, longer wait
```

### `bling.capture(session, url, *, os="win10", timeout=45) -> HAR`
Same capture, against a `Session` (opens a fresh session internally, so don't `open()` first).

### `bling.capture_here(session, url, *, timeout=45) -> HAR`
Capture on an **already-open** session, keeping its proxy/state (the session's browser can be
anything; capture launches its own Firefox in the VM). This is the one to use for cloaking
analysis: `open` → `set_proxy(country=...)` → `capture_here(url)`, then switch country and repeat.

### `bling.arm_capture(session) -> None` · `bling.sweep_captures(session) -> list[HAR]`
Multi-page capture, for click-throughs. `arm_capture` launches an instrumented Firefox that
writes a HAR on **every** page load; drive it (navigate by keyboard, click, switch proxies),
then `sweep_captures` downloads them all, oldest page first. `capture_count(session)` reports
how many have been written so far (used to tag pages by the proxy that was active).

### `bling.login(profile=None, *, wait=240) -> None`
One-time human login: opens headed Chrome; **you** solve the reCAPTCHA. The cookie persists.

### `bling.__version__` (str)

## `bling.Session`
One session, one remote VM. Manage its lifetime either way:

```python
Session(profile=None, *, headless=True)   # headless by default; headless=False to watch

with bling.Session() as s:      # context manager; releases the VM on exit (preferred)
    ...

s = bling.Session().start()     # or drive it step by step (e.g. from a REPL / the shell)
...
s.close()                       # release the VM; an atexit guard also frees it if you forget
```

| Method | What it does |
|---|---|
| `start() -> Session` | Launch the browser for step-by-step use (returns self); `with` calls this for you |
| `close()` | Release the VM and browser; idempotent, and an `atexit` guard runs it too |
| `require_login()` | Raise `NotLoggedIn` if the cookie expired |
| `is_logged_in() -> bool` | Check auth without raising |
| `open(target, *, os="win10", browser="chrome138", ready_timeout=45) -> str` | Open + wait until the canvas is up; returns `"ready"` |
| `wait_ready(timeout=45) -> str` | Poll for readiness; raises `SessionBlocked`/`NotReady` |
| `end()` | End the session via the control panel |
| **control panel (DOM)** | |
| `navigate(url, *, via="panel")` | Load a URL (`via="remote"` keeps the remote DevTools open) |
| `set_resolution("1920x1080")` | Set the remote screen resolution |
| `set_proxy(kind="datacenter", *, country=None, address=None, username=None, password=None, protocol="SOCKS5")` | Route via proxy/VPN (`datacenter`/`residential`/`mobile`/`tor`/`custom`); returns once the new exit shows and holds, see below |
| **files (curl egress)** | |
| `upload(local_path, remote_name=None) -> str` | Push a file into the VM's Downloads |
| `upload_text(text, remote_name) -> str` | Write a small text file into the VM |
| `download(remote_name, out) -> Path` | Pull a file out of the VM |
| `download_when_ready(remote_name, out, *, timeout=45) -> Path` | Download once the file exists and its size settles (for async writes like a HAR) |
| **VM control (blind)** | |
| `run(command, *, timeout=60) -> str` | Run a shell command; returns combined stdout+stderr |
| `run_script(remote_name, *, sentinel, timeout=90)` | Launch an uploaded script that spawns/long-runs; waits for its sentinel file, deleting a stale one first |
| `remove(*remote_names)` | Delete files from the VM's Downloads and confirm they are gone |
| `focus_vm()` | Give the VM keyboard focus (so Win+R etc. forward) |
| `key("Control+Shift+E")` | Press a key/chord in the focused VM window |
| `type("text")` | Type into the focused VM window |
| `canvas_click(x, y)` | Click a pixel in the remote view |
| `screenshot(path) -> Path` | Save a PNG of the session |
| `dismiss()` | Close any control-panel dialog and return focus to the VM |

Call `dismiss()` after `set_resolution()` as well as after file transfers. Setting the
resolution leaves the Display Settings dialog open over the remote view, and Escape does
not close it, so a screenshot taken straight afterwards has a Browserling dialog sitting
across the middle of whatever you were photographing.

Call it once after `open()` too. Browserling shows its own announcements ("Co-browsing
is here!", seen 2026-09-16) in a popup over the control panel, and while one is up every
click on the panel is intercepted: `end()` and `navigate()` both time out with
`menu item ... not clickable`. `dismiss()` and `end()` now tick the popup's "Don't show
again" box and close it, so a profile sees each announcement once.

**`run()` vs `run_script()`:** `run()` redirects output to a log to read it back, but a process
the command `start`s would inherit/lock that handle, so use `run_script()` (sentinel-based) for
anything that spawns an app or runs long.

**A VM can come back with an earlier session's files.** Browserling sometimes hands out the
same VM again, Downloads and all (seen 2026-09-25 across five parallel sessions). A sentinel
left there made `run_script()` return at once, before the new run had done anything, and a
`download()` then fetched the old output. `run_script()` now deletes its sentinel before
launching, and raises `BlingError` if the file will not go. The script's other outputs are
yours to clear: call `s.remove("result.txt")` first, or give each run's files a unique name.
File names passed to `run_script()` and `remove()` must be plain names (letters, digits, `.`,
`_`, `-`), because they are typed into a VM command line.

**How `set_proxy()` behaves** (the proxy panel as inspected on 2026-09-25):

- With a proxy connected, the panel reopens on that kind's screen and hides the others. To
  change kind, `set_proxy()` presses Disconnect first; before this fix, it waited 30 seconds
  on the new kind's hidden country list instead.
- "Set Location" and Tor's "New Identity" change the exit about three seconds after the
  click, with no sign in the buttons. `set_proxy()` waits for the status line to show the
  new exit, then checks it has held for a second. Before, it returned at once, and a
  Disconnect sent in that window was undone when the new exit landed.
- Browserling can refuse a change with a "Couldn't set proxy" popup, as it did for a
  residential exit in India on 2026-09-25. `set_proxy()` closes the popup, retries once,
  then raises `BlingError` quoting Browserling's message and naming the exit the session
  kept. Left open, that popup blocked every later click, so each one timed out.
- Each kind offers its own countries. Residential has no Canada; datacenter, mobile and Tor
  do. A country the kind lacks raises `BlingError` listing what that kind offers and which
  kinds have the country, and it is checked before anything is disconnected.
- Any other stall raises `BlingError` naming the step, the panel's state, and Playwright's
  call log.

## Inside the VM: the network exit

**Command-line tools inside the VM do not use the chosen exit by default.** The proxy you
set applies to the VM's browsers through the Windows proxy setting, so `curl`, `node` and
the rest go out directly unless you point them at it. That setting is `ProxyServer` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`, and it reads
`192.168.100.1:600N`, and the port is not fixed: Tor was `6004` in one session and `6002`
in another on 2026-09-25. So read it at run time in an uploaded `.bat` (`run()` refuses
double quotes). This was checked live on a Tor exit on 2026-09-25: through `%PX%`, curl
came out at the Tor address the panel showed, and without it, at the VM's own datacenter
address.

```bat
for /f "tokens=3" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer') do set PX=%%a
curl -s -x http://%PX% https://api.ipify.org > %USERPROFILE%\Downloads\exit_ip.txt
```

Node 24 is installed in the VM (`node --version` gave `v24.0.1` on 2026-09-25). Chrome there
listens on remote-debugging port 20000, so a Node script inside the VM can drive it over the
Chrome DevTools Protocol. The HAR capture deliberately avoids that route, because pages can
detect an automated browser, so use it only where that does not matter.

## `bling.HAR`
Thin, introspectable wrapper over the HAR dict.

| Member | |
|---|---|
| `HAR.load(path) -> HAR` | read a `.har` file from disk (from `bling har` or any browser's DevTools) |
| `har.entries` | list of HAR entry dicts |
| `har.creator` | producing tool, e.g. `"Firefox"` |
| `har.urls()` | every requested URL, in order |
| `har.save(path) -> Path` | write the HAR JSON |
| `har.data`, `har.url` | raw dict, source URL |
| `len(har)`, `repr(har)` | entry count / summary |

## Errors
All subclass `bling.BlingError`. Messages tell you how to fix them.

| Exception | When |
|---|---|
| `NotLoggedIn` | cookie expired → run `bling login` |
| `SessionBlocked` | plan/time/VM limit, firewall, duplicate session |
| `NotReady` | canvas didn't come up in time |
| `Timeout` | a VM op didn't finish in time |
| `EgressError` | a file transfer / curl token failed |

## CLI
```
bling login
bling har <url> [--out PATH] [--os win10] [--live] [--proxy KIND] [--country NAME]
bling urls <file.har> [--summary]
bling open <url> [--os ...] [--browser ...] [--proxy KIND] [--country NAME] [--live] [-k/--keep-open]
bling run "<command>" [--os win10] [--browser chrome138] [--live]
bling shell [--record FILE] [--play FILE] [--headless]      # interactive REPL; see docs/SHELL.md
bling play <file.bling> [--live]                            # replay a recording, unattended
bling --version

# --live          shows the browser window (default: headless)
# -k/--keep-open  leaves the session up until you close the browser window
#                 (or Ctrl-C the terminal). Implies --live, skips the closing
#                 screenshot. Use for attended flows, e.g. signing into a
#                 site inside the VM by hand. No TTY required.
# Without -k, `bling open` finishes by saving a screenshot to _explore/session.png
# (creating the folder if needed) and printing its path.
```

`bling shell` and `bling play` have their own reference: the interactive verbs, the
`.bling` recording format, and the secrets policy are all in [`SHELL.md`](SHELL.md).
