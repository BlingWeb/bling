# Environments

Which operating systems and browsers a session can be, how to name them, and what works
on each. Read live from Browserling on 2026-11-25; the lists grow, so for today's truth
ask a session: [`s.platforms()`](API.md#platforms) and [`s.browsers()`](API.md#browsers).

**See also:** [API reference](API.md) · [Advanced usage](ADVANCED.md)

## Naming an operating system

`open()`, `set_os()` and the `--os` flag take either form:

- the **name** the session menu shows: `"Windows 11"`, `"macOS 15"`, `"iOS 18"`,
  `"Android 15"`;
- the **slug** used in Browserling's addresses: the family (`win`, `mac`, `ios`,
  `android`) followed by the version, `win11`, `mac15`, `ios18`, `android15`.

bling turns a name into a slug by lower-casing it, removing spaces, and shortening
`windows` to `win` and `macos` to `mac`. A bare family (`"mac"`, `"ios"`) opens that
family's default version.

| Family | Versions (name → slug) |
|---|---|
| Windows | Windows 11 → `win11` · Windows 10 → `win10` · Windows 8.1 → `win8.1` · Windows 8 → `win8` · Windows 7 → `win7` · Windows Vista → `winvista` · Windows XP → `winxp` |
| macOS | macOS 27 → `mac27` · macOS 26 → `mac26` · macOS 15 → `mac15` · macOS 14 → `mac14` · macOS 13 → `mac13` · macOS 12 → `mac12` |
| iOS | iOS 26 → `ios26` · iOS 18 → `ios18` · iOS 17 → `ios17` · iOS 16 → `ios16` · iOS 15 → `ios15` |
| Android | Android 15, 14, 13, 12.1, 12, 11, 10, 9, 8.1, 8.0, 7.1, 7.0, 6.0, 5.1, 5.0, 4.4 → `android15` … `android12.1` … `android4.4` |

Opened and confirmed on 2026-11-25: `win10`, `win11`, `win7`, `winxp`, `mac15`, `ios18`,
`android15`. The others follow the same rule and were not each opened.

Your Browserling plan decides which of these you may open. One your plan lacks raises
`SessionBlocked`.

## Naming a browser

In `open()` and `--browser`, a browser is its lower-case name with an optional version
stuck on: `"firefox"`, `"chrome120"`, `"safari"`, `"ie"`. Without a version you get the
newest that OS has. In [`set_browser()`](API.md#set_browser) the name and version are
separate, and the name is as the menu shows it: `s.set_browser("Tor Browser", "16")`.

Newest version of each browser, per OS opened:

| OS | Browsers (newest version) |
|---|---|
| Windows 11, Windows 10 | Edge 156 · Chrome 156 · Firefox 159 · Opera 139 · Brave 1.98 · Vivaldi 8.2 · Tor Browser 16 |
| Windows 7 | Internet Explorer 11 · Supermium 132 · Chrome 109 · Firefox 115 · Opera 95 · Mypal 68 |
| Windows XP | Internet Explorer 8 · Supermium 132 · Chrome 49 · Firefox 52 · Opera 36 · Mypal 68 |
| macOS 15 | Safari 18 · Chrome 154 · Firefox 157 · Edge 154 · Opera 136 · Brave 1.96 · Vivaldi 8.2 · Tor Browser 15 |
| iOS 18 | Safari 18 · Chrome 143 · Firefox 146 · Edge 143 · Opera 6.3 · Brave 1.85 · Vivaldi 7.7 · Orion 1.4 · DuckDuckGo 7.199 |
| Android 15 | Chrome 126 · Firefox 127 |

Old versions go a long way back: Chrome on Windows 10 runs from 156 down to 1. The menu
shows the newest 15 of each browser, and `set_browser("Chrome", 49)` reaches the rest.

## What works where

| | Windows 10, 11 | Windows 7 and older | macOS | iOS | Android |
|---|---|---|---|---|---|
| `open`, `navigate`, `set_browser`, `set_os` | yes | yes | yes | yes | yes |
| `set_proxy`, `proxy_status`, `own_ip_tunnel` | yes | yes | yes | yes | yes |
| `screenshot`, `key`, `type`, `canvas_click` | yes | yes | yes | yes (a click is a tap) | yes (a click is a tap) |
| `set_scale` | yes | yes | yes | yes | yes |
| `set_resolution`, listed sizes | yes | yes | yes | no: use `set_device` | no |
| `set_resolution`, any custom size | yes | no | no | no | no |
| `upload`, `download`, `remove` | yes | yes | yes | the menu offers file transfer; not run | no |
| `run`, `run_script` | yes (Run box) | yes (Run box) | yes (Terminal) | no | no |
| `launch_app`, `apps` | Explorer, Sysinternals, Command Prompt, PowerShell, Ubuntu WSL, Kali Linux WSL | Explorer, Sysinternals, Command Prompt, PowerShell | Finder, Terminal, Xcode | none | none |
| `set_clipboard`, `get_clipboard` | yes | yes | not run | not run | not run |
| `share`, `open_tunnel` | yes | yes | yes | yes | yes |
| HAR capture (`bling.har`, `capture`, `capture_here`, `arm_capture`) | yes | not supported: it launches a Firefox 140 these systems lack | no | no | no |
| `devices`, `set_device`, `set_orientation`, `press_button`, `show_devtools` | no | no | no | yes | no |

Rows marked "yes" for Windows 10 and 11, macOS 15 and iOS 18 were run live on 2026-11-25
([`tests/test_live_panel.py`](../tests/test_live_panel.py)), the clipboard on Windows 10
only. The older Windows and Android columns come from what Browserling's menu offers on
those systems and were not each run. Where Browserling has no copy and paste for a system,
the clipboard methods raise `BlingError` saying so.

The listed screen sizes are the same everywhere they exist: 1920×1440, 1600×1200, 1280×960,
1024×768 (4:3); 1920×1080, 1600×900, 1366×768, 1280×720 (16:9); 1920×1200, 1680×1050,
1440×900, 1280×800 (16:10); 2560×1440, 2048×1536, 1280×1024; and 800×600 on systems
without custom sizes.

## Defaults

| Call | OS | Browser |
|---|---|---|
| `s.open(url)` | `win10` | `chrome138` |
| `bling.har(url)` | `win10` | Firefox, always |
| `s.set_os("Windows 11")`, `s.set_os("Android 15")` | as asked | newest Chrome |
| `s.set_os("macOS 15")`, `s.set_os("iOS 18")` | as asked | newest Safari |

`open()` pins Chrome 138 so that pixel positions used by `navigate(via="remote")` and
`focus_vm()` stay where they were measured. Pass `browser="chrome"` for the newest.

## iPhone and iPad models

An iOS session starts as that version's default device (iPhone 16e on iOS 18) and can
restart as any other with [`set_device`](API.md#set_device). iOS 18 offers 55 models;
`s.devices()` lists them. Newer hardware (iPhone 17, iPhone Air) is on iOS 26 only.
