# Errors

Every error bling raises, what causes it, and what to do. Each message is written to tell
you the fix; this page is for when you want the whole picture.

**See also:** [API reference](API.md#exceptions) · [Troubleshooting](TROUBLESHOOTING.md)

## The hierarchy

```text
BlingError                 everything bling raises; catch this to catch all
├── NotLoggedIn            no valid cookie in the profile
├── SessionBlocked         Browserling refused or ended the session
├── NotReady               the remote screen did not appear in time
├── Timeout                a command, script, file or login did not finish in time
└── EgressError            a file transfer failed
```

All of them live on the top-level package: `bling.BlingError`, `bling.NotLoggedIn` and so
on. `BlingError` is a `RuntimeError`.

```python
import bling

try:
    with bling.Session() as s:
        s.require_login()
        s.open("example.com", os="ios18", browser="safari", ready_timeout=120)
        s.set_proxy("residential", country="Canada")
except bling.NotLoggedIn:
    raise SystemExit("run: bling login")
except bling.SessionBlocked as e:
    print("Browserling said no:", e)       # plan limit, no machine, firewall
except bling.NotReady:
    print("the machine was slow to start; try again or raise ready_timeout")
except bling.BlingError as e:
    print("failed:", e)                    # includes the residential-has-no-Canada case
```

**What is not a `BlingError`.** Wrong argument *types* raise Python's own `TypeError`.
Calling a method before `start()` (outside a `with` block) raises `AttributeError`,
because there is no page yet. A local file that `upload()` cannot read raises `OSError`.
Starting a `Session` inside a Jupyter notebook raises Playwright's own error; see
[Troubleshooting](TROUBLESHOOTING.md#it-fails-inside-jupyter).

## NotLoggedIn

| Message | Cause | Fix |
|---|---|---|
| `Not logged in — run once:  bling login` | The profile folder has no cookie, or it expired | Run `bling login` and solve the CAPTCHA. For a custom `profile=`, sign that folder in: `bling.login(profile=...)` |

Raised by `require_login()`, and by `bling.har()` which calls it.

## SessionBlocked

Raised by `open()`, `wait_ready()`, `set_browser()`, `set_os()` and `set_device()` when
Browserling shows a blocking screen in place of the remote screen. The message is
`session blocked: <reason> [<screen name>]`.

| Reason in the message | Cause | Fix |
|---|---|---|
| `free daily limit reached` | A free account's daily allowance is used up | Wait, or use a paid plan |
| `developer-plan limit reached`, `team limit reached`, `plan limit reached` | Your plan's parallel-session or feature limit | End other sessions; check that no earlier run is still alive |
| `monthly limit reached` | The plan's monthly allowance is used up | Wait for the reset or change plan |
| `invalid OS/browser/url request` | Browserling does not recognise the OS, browser or version in the address | Check the names against `s.platforms()` and `s.browsers()`, or [Environments](ENVIRONMENTS.md) |
| `session time is up` | The plan's per-session time limit | Open a new session |
| `session idle-timed-out (~10 min)` | No input for about ten minutes | Open a new session; keep long waits inside `run_script()`, which nudges the session |
| `no VM available` | Browserling has no free machine of that kind | Try again, or another OS version |
| `VM shut down`, `cannot reconnect to session` | The machine died or the stream could not resume | Open a new session |
| `browser connect error`, `browser websocket error` | The stream between your Chrome and Browserling failed | Check your network; try again |
| `firewall blocked the connection` | Your network blocks Browserling's streaming ports | Try another network, or ask for the ports to be opened |
| `another host joined (duplicate session)` | The same account opened this session somewhere else | One person per account; see [Troubleshooting](TROUBLESHOOTING.md#another-host-joined) |

## NotReady

| Message | Cause | Fix |
|---|---|---|
| `session did not become ready in time` | No remote screen within the timeout, and no blocking screen either | Raise `ready_timeout` (phones and Macs can take a minute or two), or try again |

## Timeout

| Message | Raised by | Cause and fix |
|---|---|---|
| `run() timed out after Ns; partial output: ...` | `run()` | The command had not finished. Raise `timeout`, or, if it starts a program, use `run_script()` |
| `<script> did not finish (no <sentinel>) within Ns` | `run_script()` | The script never wrote its sentinel file. Check that it writes a non-empty file of exactly that name to Downloads; raise `timeout` |
| `'<name>' did not appear/settle within Ns` | `download_when_ready()` | The file was never written, or is still growing. Raise `timeout` |
| `login not completed in time — re-run: bling login` | `bling login`, `bling.login()` | The CAPTCHA was not solved within `wait` seconds |
| `'<name>.har' did not appear/settle within Ns` | `bling.har()` and the capture functions | The page produced no HAR in time. Raise `timeout`; a page that never finishes loading never writes one |

## EgressError

| Message | Cause | Fix |
|---|---|---|
| `upload of '<name>' failed: ...` | The transfer server refused or dropped the upload | Try again; check the session is still alive |
| `download of '<name>' failed: ...` | Most often the file is not in Downloads (HTTP 404) | Check the name; if the machine is still writing it, use `download_when_ready()` |
| `could not read transfer token from '...' popup` | The File Transfers window did not show its link: no file transfer on this OS (Android), or Browserling changed the page | Use Windows, Mac or iOS; otherwise [report it](../CONTRIBUTING.md) |
| `could not check '<name>' in the VM: ...` | The transfer server could not be reached | Check your network |

## BlingError raised directly

Bad arguments are refused before anything is clicked.

| Message begins | Raised by | Fix |
|---|---|---|
| `'<x>' is not an OS slug` | `open()`, `set_os()`, `capture()` | Use a Windows, macOS, iOS or Android name or slug |
| `navigate(via=...): via is 'panel' or 'remote'` | `navigate()` | Use one of the two |
| `'<x>' is not a screen size` | `set_resolution()` | Write it as `"1280x1024"` |
| `<W>x<H> is not offered on <os> and it has no custom sizes` | `set_resolution()` | Pick from the listed sizes in the message |
| `iOS has no screen sizes` | `set_resolution()` | Use `set_device()` |
| `set_scale(...): scale runs from 0 to 100` | `set_scale()` | |
| `unknown proxy kind` | `set_proxy()` | `datacenter`, `residential`, `mobile`, `tor` or `custom` |
| `no <kind> proxy location matches '<country>'` | `set_proxy()` | The message lists that kind's countries and which kinds have yours |
| `no browser '<x>' on <os>` / `no <browser> <version> on <os>` | `set_browser()` | The message lists the browsers, or the range of versions |
| `no app '<x>' in Launch apps` | `launch_app()` | The message lists the apps |
| `<os> has no Launch apps menu` | `launch_app()` | Windows and Mac only |
| `no device '<x>' on <os>` | `set_device()` | The message lists the models |
| `... is for iOS sessions` | the iOS methods | Open with `os="ios18"` |
| `set_orientation(...)`, `press_button(...)`: `one of ...` | iOS methods | Use a listed value |
| `open_tunnel(client=...): one of ssh, old-ssh, putty` | `open_tunnel()` | |
| `set_os() needs an open session` | `set_os()` | Call `open()` first |
| `command must not contain double-quotes` / `command too long for Win+R` | `run()` on Windows | Upload a script and run it by path |
| `'<x>' is not a plain filename` | `run_script()`, `remove()` | Letters, digits, `.`, `_`, `-` only; no path |
| `could not delete <names> from the VM's Downloads` | `remove()`, `run_script()` | The file is locked by a running program; close it or use another name |
| `HAR capture runs on a Windows session only` | `capture()`, `bling.har()` | Use a Windows `os` |
| `cannot read HAR` / `is not valid HAR JSON` / `not a HAR` | `HAR.load()` | Check the path and the file |
| `Browserling has no copy and paste on <os> yet` | clipboard methods | Use `type()` |

Browserling refusing, or its page not responding:

| Message begins | Meaning | Fix |
|---|---|---|
| `Browserling refused set_proxy(...) while <step>: <its words>` | Browserling's own "Couldn't set proxy" popup, after one retry. The message ends with the exit the session kept | Try another country or kind |
| `set_proxy(...) stalled while <step>` | The proxy panel stopped responding. The message carries the panel's state | Call `dismiss()` and try again; see [Troubleshooting](TROUBLESHOOTING.md#a-click-on-the-session-menu-times-out) |
| `menu item '<x>' (...) not clickable` | A session-menu button could not be clicked: a popup is over it, the OS does not have it, or no session is open | `dismiss()` first; check the OS in [Environments](ENVIRONMENTS.md) |
| `the <x> window (...) did not open` | The button was clicked and its window never appeared | As above |
| `timed out <doing something>; is a session open and the page clear?` | A step inside a method did not complete. Playwright's call log follows | As above; if it repeats, Browserling's page may have changed: [report it](../CONTRIBUTING.md) |
| `no session menu to read; call open() first` | `platforms()`, `browsers()`, `apps()` or `devices()` with no session | Call `open()` |
| `this session was joined as a guest` | `share()` in a session you joined by link | Only the host can share |

## In the shell and on the command line

`bling shell` catches every error and shows it in a red panel; you stay at the prompt.
`bling play` stops at the first failing line. The `bling` commands print the panel to
standard error and exit with status `1`. See [CLI.md](CLI.md#rules-that-hold-for-every-command).
