# bling API reference

One page. Everything public. (Docstrings carry the same info — `help(bling.har)` etc.)

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
Same capture, against a `Session` (opens a fresh session internally — don't `open()` first).

### `bling.capture_here(session, url, *, timeout=45) -> HAR`
Capture on an **already-open** session, keeping its proxy/state (the session's browser can be
anything — capture launches its own Firefox in the VM). This is the one to use for cloaking
analysis: `open` → `set_proxy(country=...)` → `capture_here(url)`, then switch country and repeat.

### `bling.login(profile=None, *, wait=240) -> None`
One-time human login — opens headed Chrome; **you** solve the reCAPTCHA. The cookie persists.

### `bling.__version__` — str

## `bling.Session`
One session, one remote VM. Manage its lifetime either way:

```python
Session(profile=None, *, headless=True)   # headless by default; headless=False to watch

with bling.Session() as s:      # context manager — releases the VM on exit (preferred)
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
| `set_proxy(kind="datacenter", *, country=None, address=None, username=None, password=None, protocol="SOCKS5")` | Route via proxy/VPN (`datacenter`/`residential`/`mobile`/`tor`/`custom`) |
| **files (curl egress)** | |
| `upload(local_path, remote_name=None) -> str` | Push a file into the VM's Downloads |
| `upload_text(text, remote_name) -> str` | Write a small text file into the VM |
| `download(remote_name, out) -> Path` | Pull a file out of the VM |
| `download_when_ready(remote_name, out, *, timeout=45) -> Path` | Download once the file exists and its size settles (for async writes like a HAR) |
| **VM control (blind)** | |
| `run(command, *, timeout=60) -> str` | Run a shell command; returns combined stdout+stderr |
| `run_script(remote_name, *, sentinel, timeout=90)` | Launch an uploaded script that spawns/long-runs; waits for its sentinel file |
| `focus_vm()` | Give the VM keyboard focus (so Win+R etc. forward) |
| `key("Control+Shift+E")` | Press a key/chord in the focused VM window |
| `type("text")` | Type into the focused VM window |
| `canvas_click(x, y)` | Click a pixel in the remote view |
| `screenshot(path) -> Path` | Save a PNG of the session |
| `dismiss()` | Close any control-panel dialog and return focus to the VM |

**`run()` vs `run_script()`:** `run()` redirects output to a log to read it back — but a process
the command `start`s would inherit/lock that handle, so use `run_script()` (sentinel-based) for
anything that spawns an app or runs long.

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
bling run "<command>" [--live]
bling shell [--record FILE] [--play FILE] [--headless]      # interactive REPL — see docs/SHELL.md
bling play <file.bling> [--live]                            # replay a recording, unattended
bling --version

# --live          shows the browser window (default: headless)
# -k/--keep-open  leaves the session up until you close the browser window
#                 (or Ctrl-C the terminal). Implies --live, skips the closing
#                 screenshot. Use for attended flows — e.g. signing into a
#                 site inside the VM by hand. No TTY required.
```
