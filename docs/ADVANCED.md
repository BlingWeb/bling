# Advanced usage

How the pieces behave in practice, and the habits that keep long runs reliable. Each
section stands alone.

**See also:** [Quickstart](QUICKSTART.md) · [API reference](API.md) ·
[Environments](ENVIRONMENTS.md) · [Troubleshooting](TROUBLESHOOTING.md)

- [The two layers](#the-two-layers)
- [Sessions and machines](#sessions-and-machines)
- [Choosing a proxy](#choosing-a-proxy)
- [How `set_proxy()` behaves](#how-set_proxy-behaves)
- [Browserling's windows and popups](#browserlings-windows-and-popups)
- [Screenshots you can read](#screenshots-you-can-read)
- [Driving the remote page](#driving-the-remote-page)
- [Running commands](#running-commands)
- [A machine can arrive with old files](#a-machine-can-arrive-with-old-files)
- [The network exit inside the machine](#the-network-exit-inside-the-machine)
- [Mac sessions](#mac-sessions)
- [iPhone and iPad sessions](#iphone-and-ipad-sessions)
- [Local servers: tunnels and Your Own IP](#local-servers-tunnels-and-your-own-ip)
- [Sharing a session](#sharing-a-session)
- [Parallel sessions](#parallel-sessions)
- [Long sweeps](#long-sweeps)
- [Secrets](#secrets)
- [Reaching past bling](#reaching-past-bling)

## The two layers

A session is two things stacked, and every method works on one or the other.

| Layer | What it is | How bling drives it | Methods |
|---|---|---|---|
| The **page** on browserling.com | Real web elements in a Chrome on your machine: the session menu, its windows, the URL box | By name, through Playwright | `open`, `navigate`, `set_proxy`, `set_browser`, `set_resolution`, `share`, `open_tunnel`, file transfer |
| The **remote screen** | The machine at Browserling, streamed as video into a canvas | Blind: keys and clicks are forwarded, nothing can be read back | `key`, `type`, `canvas_click`, `run`, `run_script`, `launch_app` |

So bling can say "set the proxy to Germany" exactly, and cannot say "click the Sign in
link". For the remote screen you look at a screenshot and click a pixel, or use the
keyboard.

`run()` gets text back from a blind screen by redirecting the command's output to a file
on the remote machine and fetching the file through Browserling's file transfer.

## Sessions and machines

- `open()` and `set_os()` start a **new machine**. Files, the proxy, tunnels and co-browsing
  are gone.
- `navigate()` and `set_browser()` keep the **same machine**. Files, the proxy and the
  transfer links survive; `set_browser()` reopens the current address in the new browser.
- A session ends when you call `end()`, leave the `with` block, or the interpreter exits.
  An exit hook closes it even if your script crashes, because a machine left running counts
  against your plan's parallel sessions.
- If a session is live when you call `open()`, it is ended first. Browserling resumes a
  live session for any new `/browse/` address, so without that step you would get the old
  machine back while your code believed it was on the new OS.

## Choosing a proxy

| Kind | Sites see | Use it for |
|---|---|---|
| none | The machine's own datacenter address (never yours) | Anything where location does not matter |
| `datacenter` | A datacenter in the country you pick; about 24 countries | Fast country switching; and deliberately, to see what a scanner is shown |
| `residential` | A home or business connection; about 6 locations | Ads, consent walls, CAPTCHAs, "what a normal visitor sees" |
| `mobile` | A phone carrier's network; about 6 countries; draws on a monthly data allowance | Mobile-only cloaking; the same cases as residential |
| `tor` | A Tor exit, optionally by country | Onion sites; how a site treats Tor |
| `custom` | Your own SOCKS or HTTP proxy | An exit you control |
| Your Own IP ([`own_ip_tunnel`](API.md#own_ip_tunnel)) | Your real address | Sites that allow-list your office; pages on your own machine |

Datacenter addresses are what security scanners use, and hostile pages know it: they serve
them fewer ads, more robot checks and, for cloaked malware, the harmless decoy. If a page
looks blank or walled from a datacenter exit, that is a result worth recording, and the
next step is `residential` or `mobile`.

Each kind has its own country list. Residential has no Canada; datacenter, mobile and Tor
do. Ask for a country a kind lacks and the error lists what it has and which kinds offer
the country. A pattern that survives refusals:

```python
for kind in ("residential", "mobile", "datacenter"):
    try:
        s.set_proxy(kind, country="India")
        break
    except bling.BlingError as e:
        print(f"{kind}: {e}")
else:
    raise SystemExit("no exit in India today")
s.wait_ready()
s.dismiss()
```

A consent wall is not a proxy problem. Google shows "Before you continue" to visitors in
many countries whatever the exit; click through it (`canvas_click` on "Reject all") rather
than changing proxies.

## How `set_proxy()` behaves

The proxy panel, as inspected live on 2026-09-25:

- With a proxy connected, the panel reopens on that kind's screen and hides the others. To
  change kind, `set_proxy()` presses Disconnect first.
- "Set Location" and Tor's "New Identity" change the exit about three seconds after the
  click, with no sign in the buttons. `set_proxy()` waits for the status line to show the
  new exit, then checks it has held for a second.
- Browserling can refuse a change with a "Couldn't set proxy" popup. `set_proxy()` closes
  the popup, retries once, then raises `BlingError` quoting Browserling's message and naming
  the exit the session kept.
- A country the kind lacks is refused before anything is disconnected, so the proxy the
  session already has is left alone.
- Any other stall raises `BlingError` naming the step, the panel's state and Playwright's
  call log.

After a change the stream restarts: call `wait_ready()`, then `dismiss()`.

## Browserling's windows and popups

Browserling's own windows float over the remote screen and stay until closed. Two kinds of
trouble follow: they appear in screenshots, and an open popup swallows every click on the
session menu.

Call `dismiss()`:

- once after `open()`, because announcements ("Co-browsing is here!") appear then;
- after `set_proxy()` and `set_resolution()`, whose windows stay open;
- after file transfers, before sending keys.

`dismiss()` ticks an announcement's "Don't show again" box, so a profile sees each one
once. The methods that open a window to read or set one thing (`set_scale`, clipboard,
tunnels, `share`, the iOS controls) close it again themselves.

## Screenshots you can read

- `screenshot(path)` is the whole page; `screenshot(path, remote_only=True)` is the remote
  screen alone.
- A screenshot is the first screen. `s.key("PageDown")`, then another, for what is below.
- Set `s.set_resolution("1280x1024")` for pages you need to read. The remote screen is
  scaled into a canvas about 1000 pixels wide, so at 1920 × 1080 small labels (an "Ad"
  badge) shrink to a few pixels. The default size is narrow enough to clip some sites'
  side columns.
- A blank or walled page is a finding. Record it as "walled", not as "nothing there".

## Driving the remote page

```python
s.focus_vm()                    # keys now go to the remote machine
s.key("Control+l")              # the remote browser's address bar
s.type("example.org/login")
s.key("Enter")
s.key("Tab"); s.type("alice")   # move between fields by keyboard
s.set_clipboard("a long or unusual string")
s.key("Control+V")
```

Prefer the keyboard: it needs no coordinates. When you must click, take a full-page
`screenshot()`, read the pixel position from the image, and `canvas_click(x, y)`; the
image's pixels are the page's pixels. Fix the screen size first if you script clicks, since
positions move with it. On phones a click is a tap; swipes and pinches are not available.

`navigate(url, via="remote")` types into the remote address bar at a fixed pixel. It is for
Chrome on Windows at the default size, when you need the remote developer tools to stay
open across a page load.

## Running commands

`run()` returns a command's output. `run_script()` starts an uploaded script and waits for
a file it writes. Choose by what the command does:

| The command | Use |
|---|---|
| Prints something and exits | `run()` |
| Is long, or needs double quotes (Windows) | Upload a `.bat` or `.py`, then `run()` it by path |
| Starts a program, or runs for minutes | `run_script()` with a sentinel file |

Why the split: `run()` redirects output to a log file to read it back, and a program the
command starts inherits that file and holds it open, so `run()` would wait for it forever.

On Windows the command goes through the Run box (Win+R), which is why it is capped near
255 characters with the wrapper and cannot hold double quotes. The remote user is an
administrator. Python, Node, Java, Git, GCC, PowerShell and WSL (Ubuntu, Kali) are on the
Windows 10 and 11 machines.

During a long `run_script()` wait, bling nudges the session every minute so that
Browserling's ten-minute idle timeout does not end it.

## A machine can arrive with old files

Browserling sometimes hands out a machine that an earlier session used, Downloads folder
and all (seen across five parallel sessions on 2026-09-25). Three consequences:

- A sentinel file left there would make `run_script()` return at once. It deletes its
  sentinel before starting, and raises if the file will not go.
- Your script's other outputs are yours to clear: `s.remove("result.txt")` first, or put a
  unique tag in each run's file names.
- A `download()` of a fixed name can fetch the previous session's copy if your script
  failed before writing its own.

## The network exit inside the machine

**Command-line tools on the remote machine do not use the proxy you set.** The proxy
applies to the machine's browsers through the Windows proxy setting, so `curl`, `node` and
the rest go out directly unless you point them at it. The setting is `ProxyServer` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`. It reads
`192.168.100.1:600N` and the port is not fixed (Tor was `6004` in one session and `6002`
in another), so read it at run time, in an uploaded `.bat` because `run()` refuses double
quotes:

```bat
for /f "tokens=3" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer') do set PX=%%a
curl -s -x http://%PX% https://api.ipify.org > %USERPROFILE%\Downloads\exit_ip.txt
```

Checked live on a Tor exit on 2026-09-25: through `%PX%`, curl came out at the Tor address
the panel showed; without it, at the machine's own address.

Chrome on the machine listens on remote-debugging port 20000, so a Node script uploaded to
the machine can drive it over the DevTools protocol. HAR capture deliberately avoids that
route, because pages can detect an automated browser.

## Mac sessions

```python
s.open("example.com", os="mac15", browser="safari", ready_timeout=120)
print(s.run("sw_vers"))
```

`run()`, `run_script()` and `remove()` look the same from your side and take a different
route. Cmd+Space never reaches Spotlight, so there is no equivalent of the Run box. bling
opens Terminal from the Launch apps list and types the command there as a zsh line:

- double quotes are allowed and there is no length cap;
- `run_script()` runs a `.py` with `python3` and anything else with `zsh`, in the
  background, so Terminal is free for the next `run()`;
- the user is `user`, files live in `~/Downloads`, `python3` and `curl` are installed and
  Node is not;
- uploading a file opens a Finder window on top; `run()` brings Terminal back first;
- screen sizes are presets only: no custom size.

HAR capture does not run on a Mac. Open the page there to see what it renders, and capture
the network on Windows.

## iPhone and iPad sessions

```python
s.open("example.com", os="ios18", browser="safari", ready_timeout=120)
s.devices()                    # ['iPhone 16e', 'iPhone 16 Plus', ...]
s.set_device("iPad (A16)")     # restarts iOS as that device
s.set_orientation(90)
s.press_button("home")
s.show_devtools()              # Safari's Web Inspector, in a Browserling window
```

What phones lack: a shell (`run`), apps (`launch_app`), screen sizes (the device decides).
Android has neither file transfer nor screen sizes. `set_scale()` works on both.

## Local servers: tunnels and Your Own IP

Two features let a session reach places only you can reach. Both hand you an SSH command
to run on your side, and both work only while that command is running.

| | Local Testing: [`open_tunnel`](API.md#open_tunnel) | Your Own IP: [`own_ip_tunnel`](API.md#own_ip_tunnel) |
|---|---|---|
| What goes through your machine | Requests to the one address you named | All of the session's browsing |
| Sites see | The session's normal exit | Your real address |
| Use it for | Testing a dev server in 40 browsers | An allow-listed site; many local addresses at once |

```python
import shlex, subprocess, time

command = s.open_tunnel("localhost:8080")
ssh = subprocess.Popen(shlex.split(command) + ["-N", "-o", "StrictHostKeyChecking=accept-new"])
while s.tunnel_stats()["status"].startswith("Waiting for connection"):
    time.sleep(2)
s.navigate("localhost:8080")
```

Checked end to end on 2026-11-25: the status went from "Waiting for connection..." to
"Connected!" within four seconds of starting `ssh`, and the request counter rose with each
page load. `close_tunnel()` makes the SSH command exit.

## Sharing a session

```python
link = s.share("Analyst", view_only=True)    # https://browserling.com/join/<id>
...
s.stop_sharing()
```

A guest who opens the link sees the live session, and with `view_only=False` can take
control of it. The link is a remote-control handle: send it over a private channel, and
call `stop_sharing()` when done. Ending the session also kills it. Chat, the guest list and
handing over control are in the session page itself; run with `headless=False` to use them.

## Parallel sessions

**One run per profile folder.** Two bling processes on the same Chrome profile kill each
other: the second one's log fills with
`TargetClosedError: Target page, context or browser has been closed`.

For parallel work, copy the signed-in profile once per worker and pass each its own:

```python
import shutil, bling
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

base = Path.home() / ".browserling_pw_profile"

def shot(country: str) -> None:
    profile = Path.home() / f".bling_profile_{country}"
    if not profile.exists():
        shutil.copytree(base, profile)
    with bling.Session(profile=str(profile)) as s:
        s.require_login()
        s.open("example.com")
        s.set_proxy("datacenter", country=country)
        s.wait_ready(); s.dismiss()
        s.screenshot(f"{country}.png", remote_only=True)

with ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(shot, ["Germany", "France", "Japan"]))
```

Your plan caps how many sessions run at once; past the cap `open()` raises
`SessionBlocked`. Copy the profile while no bling run is using it.

## Long sweeps

- Budget 30 to 60 seconds for a machine to come up and about 8 seconds per page load with a
  screenshot.
- Prefer **one session per country** over switching proxies in one session: browser state
  stays clean and every screenshot is attributable.
- Write one log line per page as you go, so a run that dies halfway still tells you what it
  saw.
- Never trigger "Print PDF locally" from an automated run. It crashed the page on
  2026-09-17.

## Secrets

Four things bling hands you are secrets for the life of the session. Keep them out of logs,
screenshots and bug reports.

| Value | From | What it allows |
|---|---|---|
| Tunnel command | `open_tunnel()`, `own_ip_tunnel()` | Attaching a machine to your session's tunnel |
| Invite link | `share()` | Watching, and usually controlling, the session |
| Transfer tokens | `transfer_token()`, `upload_token()` | Reading and writing the machine's Downloads folder |
| The profile folder | `bling login` | Your Browserling account |

A full-page screenshot taken while the Local Testing or proxy window is open shows the
tunnel command. Use `remote_only=True`, or close the window first.

A HAR is sensitive too: it holds cookies, `Authorization` headers and form posts in plain
text. See the [security notes](../README.md#security).

## Reaching past bling

`s.page` is the Playwright page for browserling.com. Anything bling does not wrap, you can
click yourself:

```python
s.page.locator(".button-item.send-feedback").click()      # opens the feedback popup
```

The selectors bling itself uses are in `bling/config.py`, in one place so they are easy to
fix when Browserling changes its page. [What bling does not drive](API.md#what-bling-does-not-drive)
lists the features left out and why.
