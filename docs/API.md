# bling API reference

Everything public, with every parameter, return value and error. Docstrings carry a short
form of the same thing: `help(bling.Session.set_proxy)`.

**See also:** [Quickstart](QUICKSTART.md) · [Advanced usage](ADVANCED.md) ·
[Errors](ERRORS.md) · [Environments](ENVIRONMENTS.md) · [Command line](CLI.md) ·
[`bling shell`](SHELL.md) · [Troubleshooting](TROUBLESHOOTING.md)

```python
import bling
```

## Contents

- [The one-liner](#the-one-liner): [`bling.har`](#har)
- [Capture functions](#capture-functions): [`capture`](#capture) ·
  [`capture_here`](#capture_here) · [`arm_capture`](#arm_capture) ·
  [`sweep_captures`](#sweep_captures) · [`capture_count`](#capture_count)
- [`bling.login`](#login)
- [`bling.Session`](#session)
  - [Lifetime](#lifetime): [`start`](#start) · [`close`](#close) ·
    [`is_logged_in`](#is_logged_in) · [`require_login`](#require_login)
  - [Opening and ending](#opening-and-ending): [`open`](#open) ·
    [`wait_ready`](#wait_ready) · [`end`](#end) · [`navigate`](#navigate)
  - [Operating system and browser](#operating-system-and-browser):
    [`platform`](#platform) · [`is_mac`](#is_mac) · [`platforms`](#platforms) ·
    [`browsers`](#browsers) · [`set_browser`](#set_browser) · [`set_os`](#set_os)
  - [Screen](#screen): [`set_resolution`](#set_resolution) · [`set_scale`](#set_scale) ·
    [`screenshot`](#screenshot) · [`dismiss`](#dismiss)
  - [Proxy](#proxy): [`set_proxy`](#set_proxy) · [`proxy_status`](#proxy_status) ·
    [`own_ip_tunnel`](#own_ip_tunnel)
  - [Local testing](#local-testing): [`open_tunnel`](#open_tunnel) ·
    [`tunnel_stats`](#tunnel_stats) · [`close_tunnel`](#close_tunnel)
  - [Files](#files): [`upload`](#upload) · [`upload_text`](#upload_text) ·
    [`upload_bytes`](#upload_bytes) · [`download`](#download) ·
    [`download_when_ready`](#download_when_ready) · [`remove`](#remove) ·
    [`transfer_token`](#transfer_token) · [`upload_token`](#upload_token)
  - [Commands and apps](#commands-and-apps): [`run`](#run) · [`run_script`](#run_script) ·
    [`apps`](#apps) · [`launch_app`](#launch_app)
  - [Keyboard, mouse and clipboard](#keyboard-mouse-and-clipboard):
    [`focus_vm`](#focus_vm) · [`key`](#key) · [`type`](#type) ·
    [`canvas_click`](#canvas_click) · [`set_clipboard`](#set_clipboard) ·
    [`get_clipboard`](#get_clipboard)
  - [Co-browsing](#co-browsing): [`share`](#share) · [`stop_sharing`](#stop_sharing)
  - [iOS](#ios): [`devices`](#devices) · [`set_device`](#set_device) ·
    [`set_orientation`](#set_orientation) · [`press_button`](#press_button) ·
    [`show_devtools`](#show_devtools)
  - [Attributes](#attributes)
- [`bling.HAR`](#blinghar)
- [Exceptions](#exceptions)
- [What bling does not drive](#what-bling-does-not-drive)

Two words used throughout. The **session** is one remote machine with one browser on it,
streamed into a page on browserling.com. The **session menu** is Browserling's floating
panel on that page (Display options, Proxy and VPN, File transfers and the rest); most
`Session` methods click it for you.

---

`bling.__version__` is the installed version, a string such as `"0.2.0"`.

## The one-liner

### har

```python
bling.har(url, *, out=None, os="win10", timeout=45, live=False, profile=None) -> HAR
```

Record every request a page makes, from a real Firefox on a throwaway Windows machine.
Nothing about the browser is automated from the inside (no DevTools protocol), so the page
cannot tell it is being watched. Opens its own session and ends it.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `url` | `str` | required | The page to load. A bare host (`example.com`) is fine |
| `out` | path or `None` | `None` | Also write the HAR to this file |
| `os` | `str` | `"win10"` | The Windows version, as a slug or a name. Capture is Windows only |
| `timeout` | `int` | `45` | Seconds to wait for the capture to appear and stop growing |
| `live` | `bool` | `False` | Show the browser window instead of running headless |
| `profile` | `str` or `None` | `None` | Profile folder holding the login cookie; `None` uses `~/.browserling_pw_profile` |

**Returns** a [`HAR`](#blinghar).

**Raises** `NotLoggedIn` (run `bling login`), `SessionBlocked` or `NotReady` (the session
did not come up), `Timeout` (no capture within `timeout`), `EgressError` (the file could not
be fetched), `BlingError` (a non-Windows `os`).

```python
h = bling.har("demo.browserling.com", out="demo.har")
len(h), h.creator            # (7, 'Firefox')
```

## Capture functions

These record on a `Session` you manage, which is how you capture through a proxy or across
several pages.

### capture

```python
bling.capture(session, url, *, os="win10", timeout=45) -> HAR
```

The same capture as [`har`](#har), on your `Session`. It calls `session.open()` itself, so
do not open first: a session you had open is ended, and its proxy with it. To keep a proxy,
use [`capture_here`](#capture_here).

**Raises** as [`har`](#har), minus `NotLoggedIn` (it does not check; call
`require_login()` yourself).

```python
with bling.Session() as s:
    s.require_login()
    h = bling.capture(s, "demo.browserling.com")
```

### capture_here

```python
bling.capture_here(session, url, *, timeout=45) -> HAR
```

Capture on the session that is **already open**, keeping its proxy and files. The session's
own browser can be anything; capture starts its own Firefox on the machine. Windows
sessions only. This is the one for geo-cloaking work: set a country, capture, switch,
capture again.

```python
with bling.Session() as s:
    s.require_login()
    s.open("example.com")
    s.set_proxy("datacenter", country="Germany")
    de = bling.capture_here(s, "suspicious.example")
    s.set_proxy("datacenter", country="United States")
    us = bling.capture_here(s, "suspicious.example")
only_in_germany = set(de.urls()) - set(us.urls())
```

### arm_capture

```python
bling.arm_capture(session) -> None
```

Start multi-page capture on an open Windows session: an instrumented Firefox that writes
one HAR for **every** page it loads. Drive it afterwards with `key`, `type` and
`canvas_click` (its address bar is reached with `key("Control+l")`), switch proxies as you
go, then collect with [`sweep_captures`](#sweep_captures).

### sweep_captures

```python
bling.sweep_captures(session) -> list[HAR]
```

Download every HAR written since `arm_capture`, oldest page first.

### capture_count

```python
bling.capture_count(session) -> int
```

How many HARs the armed Firefox has written so far. Call it before a proxy switch to know
which pages were served through which exit.

```python
bling.arm_capture(s)
...                                  # click through the funnel
marker = bling.capture_count(s)      # pages [0, marker) used the first exit
s.set_proxy("residential", country="Germany")
...
hars = bling.sweep_captures(s)
```

## login

```python
bling.login(profile=None, *, wait=240) -> None
```

The one-time sign-in. Opens a visible Chrome on Browserling's sign-in form; **you** solve
the reCAPTCHA and press Continue. bling never solves it. The cookie is saved in the profile
folder and every later call runs unattended until it expires.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `profile` | `str` or `None` | `None` | Profile folder to sign in; `None` is `~/.browserling_pw_profile` |
| `wait` | `int` | `240` | Seconds to wait for you to finish |

If `BROWSERLING_EMAIL` and `BROWSERLING_PASSWORD` are set (in the environment, or in a
`.env` or `keys.env` file in the current folder or above), the form is filled in for you.

**Raises** `Timeout` if you do not finish within `wait`.

---

## Session

```python
bling.Session(profile=None, *, headless=True)
```

One session: one remote machine, driven through one local Chrome window.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `profile` | `str` or `None` | `None` | Chrome profile folder holding the login cookie. Give each parallel run its own copy; two runs on one folder kill each other ([Advanced](ADVANCED.md#parallel-sessions)) |
| `headless` | `bool` | `True` | `False` shows the Chrome window so you can watch |

Use it as a context manager, so the remote machine is always released:

```python
with bling.Session() as s:
    s.require_login()
    s.open("example.com")
    print(s.run("whoami"))
```

Or step by step, in a REPL:

```python
s = bling.Session(headless=False).start()
s.require_login()
s.open("example.com")
s.close()
```

A `Session` cannot run inside a Jupyter notebook: Playwright's synchronous mode refuses to
start inside a running event loop. Plain `python` and terminal IPython are fine.

### Lifetime

#### start

```python
s.start() -> Session
```

Launch Chrome on the profile and return `self`. The context manager calls it for you.
Calling it twice is harmless. It registers an exit hook, so an interpreter you abandon
still releases the remote machine.

#### close

```python
s.close() -> None
```

Close Chrome, which ends the session. Safe to call more than once; the context manager and
the exit hook both call it.

#### is_logged_in

```python
s.is_logged_in() -> bool
```

Load browserling.com and report whether the saved cookie still signs you in. It navigates
the page, so call it before `open()`, not during a session.

**Raises** `BlingError` if browserling.com does not load within 30 seconds.

#### require_login

```python
s.require_login() -> None
```

**Raises** `NotLoggedIn`, with the fix in the message, when `is_logged_in()` is false.
Call it once after `start()`.

### Opening and ending

#### open

```python
s.open(target, *, os="win10", browser="chrome138", ready_timeout=45) -> str
```

Start a fresh machine at `target` and wait until its screen is streaming.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `target` | `str` | required | The address to open |
| `os` | `str` | `"win10"` | A slug (`"win11"`, `"mac15"`, `"ios18"`, `"android15"`) or the menu's name (`"Windows 11"`). A bare family (`"mac"`) opens its default version. [All of them](ENVIRONMENTS.md) |
| `browser` | `str` | `"chrome138"` | A browser name with an optional version: `"firefox"`, `"chrome120"`, `"safari"`. No version means the newest |
| `ready_timeout` | `int` | `45` | Seconds to wait for the screen. Give phones and Macs 120 |

**Returns** `"ready"`.

**Raises** `BlingError` (an `os` that is not Windows, macOS, iOS or Android; the page did
not load), `SessionBlocked` (Browserling refused: plan limit, no machine, firewall; the
message says which), `NotReady` (no screen within `ready_timeout`).

Every call starts a new machine: files, the proxy and any tunnel from the previous one are
gone. If a session is already live on the page, `open()` ends it first; otherwise
Browserling hands the old machine back whatever you asked for.

```python
s.open("example.com")
s.open("example.com", os="Windows 11", browser="firefox")
s.open("example.com", os="ios18", browser="safari", ready_timeout=120)
```

#### wait_ready

```python
s.wait_ready(timeout=45) -> str
```

Wait until the remote screen is streaming. `open()`, `set_browser()` and `set_os()` call
it for you; call it yourself after `set_proxy()`, which restarts the stream.

**Returns** `"ready"`. **Raises** `SessionBlocked`, `NotReady`.

#### end

```python
s.end() -> None
```

End the session from the session menu. It ends at once, with no confirmation, and the page
returns to Browserling's home. Leaving the `with` block also ends it.

**Raises** `BlingError` if the menu's End button cannot be clicked (no session open).

#### navigate

```python
s.navigate(url, *, via="panel") -> None
```

Load another address on the same machine, keeping its proxy and files.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `url` | `str` | required | The address to load |
| `via` | `str` | `"panel"` | `"panel"` uses the session menu's URL box. `"remote"` types into the remote browser's own address bar, which keeps its developer tools open; it clicks a fixed pixel, so it suits Chrome on Windows at the default size |

**Raises** `BlingError` (a `via` that is neither, or the URL box is not reachable).

### Operating system and browser

#### platform

```python
s.platform -> str
```

The open session's OS family: `"win"`, `"mac"`, `"ios"` or `"android"`.

#### is_mac

```python
s.is_mac -> bool
```

`True` on a macOS session, where commands go through Terminal instead of the Run box.

#### platforms

```python
s.platforms() -> list[str]
```

Every operating system the session menu offers, as it names them (`"Windows 11"`,
`"macOS 15"`, `"iOS 18"`, `"Android 15"`), read live. Any of these names works as `os` in
`open()` and `set_os()`. Needs an open session.

#### browsers

```python
s.browsers() -> dict[str, list[str]]
```

The browsers the current OS offers, each with the versions its list shows, newest first.
The menu shows each browser's newest 15 versions; older ones exist and
[`set_browser`](#set_browser) reaches them.

```python
s.browsers()["Firefox"][:3]      # ['159', '158', '157']
```

#### set_browser

```python
s.set_browser(name, version=None, *, ready_timeout=60) -> str
```

Switch to another browser **on the same machine** and reopen the current address. The
machine's files, its proxy and its transfer links all carry over.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required | The browser as the menu names it, any case: `"Firefox"`, `"Tor Browser"` |
| `version` | `str`, `int` or `None` | `None` | The version as the menu shows it; `None` is the newest |
| `ready_timeout` | `int` | `60` | Seconds to wait for the screen afterwards |

**Returns** `"ready"`. **Raises** `BlingError` naming the browsers on offer (unknown
browser) or the range of versions (unknown version); `NotReady`.

```python
s.set_browser("Firefox")
s.set_browser("Chrome", 100)
```

#### set_os

```python
s.set_os(os, *, browser=None, ready_timeout=120) -> str
```

Move the session to another operating system, at the address it is on now. Browserling
starts a new machine for a new OS, so this ends the session and opens another: **files,
the proxy and tunnels do not carry over**.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `os` | `str` | required | A slug or a menu name, as in [`open`](#open) |
| `browser` | `str` or `None` | `None` | The browser to open with. `None` picks Chrome on Windows and Android, Safari on macOS and iOS |
| `ready_timeout` | `int` | `120` | Seconds to wait for the screen |

**Returns** `"ready"`. **Raises** `BlingError` (no session open yet; unknown OS family),
`SessionBlocked`, `NotReady`.

```python
s.set_os("Windows 11")
s.set_os("mac15", browser="firefox")
```

### Screen

#### set_resolution

```python
s.set_resolution(value) -> None
```

Set the remote screen's size. `value` is `"WIDTHxHEIGHT"`, such as `"1280x1024"`. A size
on the menu's list is clicked; any other, from 100 to 4096 a side, is set as a custom size
where the OS allows it (Windows 10 and 11).

**Raises** `BlingError` (not a size; iOS, which has devices instead of sizes; a size the
OS does not offer where custom sizes are not available, with the list of what is).

The Display Settings window stays open over the page afterwards. Call
[`dismiss()`](#dismiss) before a screenshot.

#### set_scale

```python
s.set_scale(percent) -> None
```

Magnify the remote screen inside the page, from `0` (fit, the default) to `100`. This
zooms and crops the view; the remote screen keeps its size.

**Raises** `BlingError` outside 0 to 100.

#### screenshot

```python
s.screenshot(path, *, remote_only=False) -> Path
```

Save a PNG of the session. By default it is the whole page: the remote screen with the
session menu and any open Browserling window over it. `remote_only=True` saves the remote
screen alone, hiding the menu and windows for the shot and leaving them as they were.

**Returns** the path. Parent folders are created.

A screenshot is the first screen only. Press `s.key("PageDown")` and take another for the
rest of a page.

#### dismiss

```python
s.dismiss() -> None
```

Close every open Browserling window and popup and give the keyboard back to the remote
machine. Call it after `open()` (Browserling's announcements appear then), after
`set_proxy()` and `set_resolution()` (their windows stay open), and after file transfers.

### Proxy

#### set_proxy

```python
s.set_proxy(kind="datacenter", *, country=None, address=None,
            username=None, password=None, protocol="SOCKS5") -> None
```

Route the session's browsers through another network. It returns once the proxy panel
shows the new exit and the exit has held for a second.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `kind` | `str` | `"datacenter"` | `"datacenter"`, `"residential"`, `"mobile"`, `"tor"` or `"custom"` |
| `country` | `str` or `None` | `None` | Exit country, matched anywhere in the name without regard to case. `None` keeps the kind's default |
| `address` | `str` or `None` | `None` | `custom` only: `host:port` of your proxy |
| `username`, `password` | `str` or `None` | `None` | `custom` only |
| `protocol` | `str` | `"SOCKS5"` | `custom` only: `"SOCKS5"`, `"SOCKS4"` or `"HTTP"` |

**Raises** `BlingError`, always with the reason: an unknown kind; a country the kind does
not offer (the message lists what it does offer and which kinds have that country, and
nothing is disconnected); Browserling's own refusal, quoted, after one retry; or a stall,
naming the step and the panel's state.

Mobile exits draw on a monthly data allowance. Use `residential` or `mobile` for anything
about ads, consent walls or "what a normal visitor sees"; datacenter addresses are what
scanners use and sites treat them differently. More in
[Advanced](ADVANCED.md#choosing-a-proxy).

```python
s.set_proxy("residential", country="germany")
s.wait_ready()
s.dismiss()
```

For the "Your Own IP" kind, see [`own_ip_tunnel`](#own_ip_tunnel).

#### proxy_status

```python
s.proxy_status() -> dict
```

What the session is browsing through now.

**Returns** `{"kind": str, "status": str, "connected": bool}`. `kind` is the panel's label
(`"Not using proxy"`, `"Datacenter IP"`, `"Residential IP"`, `"Tor Exit Node"`).
`status` is the panel's whole status line, with the exit address and the network that owns
it. With no proxy the address is the session's own Browserling address, never yours.

```python
s.proxy_status()
# {'kind': 'Datacenter IP',
#  'status': 'Datacenter IP | 🇩🇪172.236.212.97 | Akamai Connected Cloud (linode.com, AS63949)',
#  'connected': True}
```

#### own_ip_tunnel

```python
s.own_ip_tunnel() -> str
```

Start Browserling's "Your Own IP" proxy and **return the SSH command that brings it up**.
Run the command on your own machine and keep it running: the session then browses through
your connection, so sites see your real address, and the session can open `localhost` on
your machine. `proxy_status()` shows when it is connected. Calling `set_proxy()` with
another kind backs out of it.

The command holds a per-session secret. Do not log it or paste it anywhere public.

**Raises** `BlingError` if the proxy panel will not open the screen.

### Local testing

A tunnel lets the session open a web server only you can reach: one on your laptop, or on
your office network.

#### open_tunnel

```python
s.open_tunnel(address="localhost:8080", *, client="ssh") -> str
```

Open a tunnel to `address` and **return the command that connects it**. Run the command on
a machine that can reach `address` and keep it running; then `s.navigate(address)` loads
your server inside the session.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `address` | `str` | `"localhost:8080"` | `host:port` of the server, as seen from where you will run the command |
| `client` | `str` | `"ssh"` | The command's form: `"ssh"` (OpenSSH 7.6 or later), `"old-ssh"` or `"putty"` |

**Returns** the command, such as `ssh -R 2 <id>:<secret>@tunnel.browserling.com`. It holds
a per-session secret.

**Raises** `BlingError` (unknown `client`; the Local Testing window did not respond).

An open tunnel is closed first, so calling this again moves the tunnel.

```python
import shlex, subprocess

command = s.open_tunnel("localhost:8080")
ssh = subprocess.Popen(shlex.split(command) + ["-N"])
s.navigate("localhost:8080")
print(s.tunnel_stats())     # {'open': True, 'status': 'Connected!', 'requests': 2, ...}
s.close_tunnel()
ssh.terminate()
```

#### tunnel_stats

```python
s.tunnel_stats() -> dict
```

**Returns** `{"open": bool, "status": str, "requests": int, "in": str, "out": str}`.
`status` reads `"Waiting for connection..."` until your command connects, then
`"Waiting for server..."` and `"Connected!"`. `in` and `out` are sizes as Browserling
prints them (`"1.6 KB"`).

#### close_tunnel

```python
s.close_tunnel() -> None
```

Close the tunnel; your SSH command then exits by itself. Safe to call when none is open.

### Files

Files move through a per-session link on Browserling's transfer servers. On the remote
machine they live in the user's Downloads folder (`C:\Users\user\Downloads`, or
`~/Downloads` on a Mac), and Browserling deletes them when the session ends. Android has no
file transfer; on iOS the menu offers it but bling's file methods have not been run there.

#### upload

```python
s.upload(local_path, remote_name=None) -> str
```

Copy a local file into the remote Downloads folder. `remote_name` defaults to the file's
own name. **Returns** the remote name. **Raises** `EgressError`.

#### upload_text

```python
s.upload_text(text, remote_name) -> str
```

Write `text` (UTF-8) as a file in the remote Downloads folder: a script, a config.
**Returns** the remote name. **Raises** `EgressError`.

#### upload_bytes

```python
s.upload_bytes(data, remote_name) -> str
```

Write raw `bytes` as a remote file. `upload` and `upload_text` are thin wrappers over
this. **Returns** the remote name. **Raises** `EgressError`.

#### download

```python
s.download(remote_name, out) -> Path
```

Copy a file from the remote Downloads folder to local path `out`. **Returns** `out` as a
`Path`. **Raises** `EgressError` if the file is not there or the transfer fails.

#### download_when_ready

```python
s.download_when_ready(remote_name, out, *, timeout=45, poll=3.0) -> Path
```

Download a file the remote machine is still writing: wait until it exists and its size has
been the same for two checks in a row.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `remote_name` | `str` | required | The file's name in the remote Downloads folder |
| `out` | path | required | Where to save it locally |
| `timeout` | `int` | `45` | Seconds to wait in all |
| `poll` | `float` | `3.0` | Seconds between checks |

**Returns** `out` as a `Path`. **Raises** `Timeout`.

#### remove

```python
s.remove(*remote_names) -> None
```

Delete files from the remote Downloads folder and confirm they are gone. Names must be
plain file names (letters, digits, `.`, `_`, `-`), because they are typed into a command
line. **Raises** `BlingError` (a name with a path or other characters; a file that is
still there afterwards), `Timeout`, `EgressError`.

Browserling can hand out a machine an earlier session used, files and all. Clear a
script's output files before running it again
([Advanced](ADVANCED.md#a-machine-can-arrive-with-old-files)).

#### transfer_token

```python
s.transfer_token() -> tuple[str, str]
```

**Returns** `(server, token)` for downloads: files are served at
`https://<server>/<token>/<name>`. Read from the File Transfers window once per machine
and cached. Use it to fetch a file with your own HTTP client. **Raises** `EgressError`.

#### upload_token

```python
s.upload_token() -> tuple[str, str]
```

**Returns** `(server, token)` for uploads: `PUT` a file to
`https://<server>/<token>/<name>`. Cached likewise. **Raises** `EgressError`.

Both tokens are secrets for the life of the session: anyone holding one can read or write
that machine's Downloads folder.

### Commands and apps

#### run

```python
s.run(command, *, timeout=60, poll=2.0) -> str
```

Run a shell command on the remote machine and return what it printed, standard output and
standard error together. Windows and Mac sessions only.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `command` | `str` | required | On Windows, a `cmd` line: at most about 200 characters and **no double quotes**, because it goes through the Run box. On a Mac, a zsh line with neither limit |
| `timeout` | `int` | `60` | Seconds to wait for the command to finish |
| `poll` | `float` | `2.0` | Seconds between checks for its output |

**Returns** the output, stripped of trailing whitespace.

**Raises** `BlingError` (a double quote, or too long, on Windows), `Timeout` (with the
output so far in the message), `EgressError`.

The remote user is an administrator on Windows. For anything long, quoted, or that starts
another program, upload a script and use [`run_script`](#run_script).

```python
s.run("whoami")                      # 'win10\\user'
s.run("powershell -c Get-Clipboard")
```

#### run_script

```python
s.run_script(remote_name, *, sentinel, timeout=90, poll=3.0) -> None
```

Start an uploaded script and wait until it creates a file named `sentinel` in Downloads.
Use it instead of `run()` for scripts that open programs or run long: `run()` captures
output through a log file, and a program the script starts would hold that file open.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `remote_name` | `str` | required | The script in the remote Downloads folder. Windows runs it with `cmd /c` (`.bat`, `.py`, `.ps1` by file association). A Mac runs `.py` with `python3` and anything else with `zsh`, in the background |
| `sentinel` | `str` | required | A plain file name your script writes, non-empty, when it has finished |
| `timeout` | `int` | `90` | Seconds to wait for the sentinel |
| `poll` | `float` | `3.0` | Seconds between checks |

**Raises** `BlingError` (a `sentinel` that is not a plain file name; a stale sentinel that
cannot be deleted), `Timeout`, `EgressError`.

A sentinel left by an earlier session is deleted before the script starts. The script's
other output files are yours to clear with [`remove`](#remove).

```python
s.upload_text("@echo off\nver > %USERPROFILE%\\Downloads\\ver.txt\n"
              "echo done > %USERPROFILE%\\Downloads\\ver.done\n", "ver.bat")
s.run_script("ver.bat", sentinel="ver.done")
s.download("ver.txt", "ver.txt")
```

#### apps

```python
s.apps() -> list[str]
```

The apps `launch_app` can open in this session. Windows 10 and 11: `Explorer`,
`Sysinternals`, `Command Prompt`, `PowerShell`, `Ubuntu WSL`, `Kali Linux WSL`. Mac:
`Finder`, `Terminal`, `Xcode`. Phones: none.

#### launch_app

```python
s.launch_app(name) -> None
```

Open an app from the session menu's Launch apps list, or bring it forward if it is open.
Command Prompt and PowerShell open as administrator. **Raises** `BlingError` (no such app,
listing the ones on offer; an iOS or Android session).

### Keyboard, mouse and clipboard

The remote screen is a picture. Keys and clicks are forwarded to it; nothing about the
page inside can be read back as text, so look at a [`screenshot`](#screenshot) to find
where to click.

#### focus_vm

```python
s.focus_vm(point=None) -> None
```

Click the remote screen so that keys go to the remote machine. `point` is an `(x, y)` page
pixel; by default it is a spot that changes nothing (the browser's tab strip on Windows,
the menu bar on a Mac). `dismiss()` does this for you.

#### key

```python
s.key(combo) -> None
```

Press a key or chord on the remote machine: `"Enter"`, `"PageDown"`, `"Control+L"`,
`"Meta+r"` (the Windows key). Names follow
[Playwright's](https://playwright.dev/python/docs/api/class-keyboard#keyboard-press).

#### type

```python
s.type(text, *, delay=20) -> None
```

Type `text` on the remote machine, `delay` milliseconds between keys.

#### canvas_click

```python
s.canvas_click(x, y, *, clicks=1) -> None
```

Click page pixel `(x, y)`, which lands on whatever the remote screen shows there. On
phones a click is a tap. `clicks=2` double-clicks. Coordinates are for the page at its
fixed 1500 × 850 size, the same pixels a full-page `screenshot()` has.

#### set_clipboard

```python
s.set_clipboard(text) -> None
```

Put `text` on the remote machine's clipboard. Paste it with `s.key("Control+V")`. Faster
and more exact than `type()` for long or unusual text. **Raises** `BlingError` on an OS
where Browserling has no copy and paste.

#### get_clipboard

```python
s.get_clipboard() -> str
```

**Returns** the text on the remote clipboard, as Browserling's Clipboard Manager shows it.
Text copied in the session reaches the manager a second or two later, so wait a moment
after the copy.

bling grants the Browserling page no clipboard permission, so **your own clipboard is never
read or written** by either method.

### Co-browsing

#### share

```python
s.share(name="bling", *, view_only=False) -> str
```

Start co-browsing and **return the invite link**. Anyone who opens the link joins the live
session.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | `"bling"` | Your name, shown to guests |
| `view_only` | `bool` | `False` | `True`: guests watch until you hand them control. `False`: guests can take control at any time |

**Returns** a link of the form `https://browserling.com/join/<id>`. While a session is
shared, calling `share()` again returns the same link.

**Raises** `BlingError` (the session was joined as a guest; the popup did not respond).

Treat the link as a secret: it is a remote-control handle on the machine.

#### stop_sharing

```python
s.stop_sharing() -> None
```

Stop co-browsing: the link stops working and every guest is removed. The session carries
on for you. Safe to call when nothing is shared.

### iOS

These need an iOS session (`s.open(url, os="ios18", browser="safari")`) and raise
`BlingError` on any other.

#### devices

```python
s.devices() -> list[str]
```

Every iPhone and iPad model this iOS version offers, as the Device Model list names them.

#### set_device

```python
s.set_device(model, *, ready_timeout=180) -> str
```

Restart the session as another device model, such as `"iPhone 15"` or `"iPad (A16)"`.
Browserling restarts iOS on the new device; files and apps on the old one are not copied.
**Returns** `"ready"`. **Raises** `BlingError` (unknown model, listing the ones on offer),
`NotReady`.

#### set_orientation

```python
s.set_orientation(degrees) -> None
```

Rotate the device: `0` (portrait), `90`, `180` or `270`. **Raises** `BlingError` for any
other angle.

#### press_button

```python
s.press_button(name) -> None
```

Press a device button: `"home"`, `"action"`, `"mute"`, `"volume_down"`, `"volume_up"` or
`"lock"`. **Raises** `BlingError` for any other name.

#### show_devtools

```python
s.show_devtools() -> None
```

Open Safari's Web Inspector for the page, in a Browserling window over the session. On
desktop sessions use the browser's own tools: `s.key("F12")`.

### Attributes

| Attribute | Type | Meaning |
|---|---|---|
| `s.page` | Playwright `Page` | The local browserling.com page. An escape hatch for anything bling does not wrap; it exists after `start()` |
| `s.profile` | `str` | The profile folder in use |
| `s.headless` | `bool` | Whether Chrome is hidden |
| `s.os` | `str` | The slug of the OS last opened (`"win10"` before any `open()`) |

---

## bling.HAR

A small wrapper over a HAR file's JSON. You get one from the capture functions or from
`HAR.load`.

| Member | Type | Meaning |
|---|---|---|
| `HAR.load(path)` | class method → `HAR` | Read a `.har` file, from bling or from any browser's developer tools. **Raises** `BlingError` if the file is missing or is not a HAR |
| `har.entries` | `list[dict]` | The HAR's entries, one per request, in order |
| `har.creator` | `str` | The tool that wrote it, such as `"Firefox"` |
| `har.urls()` | `list[str]` | Every requested URL, in order |
| `har.save(path)` | → `Path` | Write the HAR as JSON; creates parent folders |
| `har.data` | `dict` | The raw HAR |
| `har.url` | `str` | The address that was captured (for a loaded file, its first request's address) |
| `len(har)` | `int` | Number of entries |
| `repr(har)` | `str` | `<HAR 'demo.browserling.com' 7 entries, creator Firefox>` |

A HAR holds cookies, `Authorization` headers and form posts in plain text, and a list of
live, possibly hostile URLs. Read the [security notes](../README.md#security) before
sharing or opening one.

## Exceptions

Every error bling raises is a `bling.BlingError`, and each message says what to do next.
[ERRORS.md](ERRORS.md) lists each message with its cause and fix.

| Exception | Raised when |
|---|---|
| `BlingError` | Base class. Also raised directly for bad arguments, a refused proxy, and a session menu that does not respond |
| `NotLoggedIn` | The profile has no valid cookie. Run `bling login` |
| `SessionBlocked` | Browserling refused or ended the session: plan or time limit, no machine available, firewall, the same session opened elsewhere |
| `NotReady` | The remote screen did not appear in time |
| `Timeout` | A command, a script, a file or the login did not finish in time |
| `EgressError` | A file transfer failed, or its link could not be read |

```python
try:
    h = bling.har("example.com")
except bling.NotLoggedIn:
    raise SystemExit("run: bling login")
except bling.SessionBlocked as e:
    print("Browserling said no:", e)
except bling.BlingError as e:
    print("failed:", e)
```

## What bling does not drive

Browserling features left out on purpose, and what to do instead:

| Feature | Instead |
|---|---|
| Capture screen's annotation editor, Save to account, Upload to Imgur | `screenshot(path, remote_only=True)`; annotate locally. Publishing a capture is a person's decision |
| On-screen keyboard | `key()` and `type()` send any key |
| Send feedback | It sends a message to Browserling; do that by hand |
| The File Explorer right-click tools (hash, VirusTotal lookup, hex editor) | `run("certutil -hashfile ...")` and tools you upload; the menu is pixels on the remote screen |
| Co-browsing chat and handing control to a guest | By hand in the session page (`headless=False`) |
| Drag-and-drop and folder upload | `upload()` per file |
| Android screen sizes, file transfer and apps | Browserling does not offer them on Android |
| Live API (embedding sessions in your own site) | A separate Browserling product with its own key |

For anything else on the page, `s.page` is the Playwright page: you can click the session
menu yourself.
