# Changelog

Every user-visible change, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com); versions follow
[semantic versioning](https://semver.org). While bling is `0.x`, a minor version may change
behaviour, and this file says how.

## 0.2.0 (2026-11-25)

The rest of Browserling's session menu, and a full set of docs. Every new method was run
against live sessions on the release date (`tests/test_live_panel.py`).

### Added

- **Switch browser in place:** `Session.set_browser(name, version)` changes browser on the
  same machine, keeping files and proxy. `browsers()` lists what the OS offers.
- **Switch operating system:** `Session.set_os(os)` moves to another OS at the current
  address. `platforms()` lists every OS.
- **OS names:** `open()`, `set_os()` and `--os` accept the menu's names (`"Windows 11"`,
  `"macOS 15"`) as well as slugs. `Session.platform` reports the family.
- **Screen:** `set_scale(percent)`; `set_resolution()` sets custom sizes on Windows 10 and
  11; `screenshot(path, remote_only=True)` saves the remote screen without Browserling's
  menu and windows.
- **Clipboard:** `set_clipboard(text)` and `get_clipboard()`, without touching your own
  clipboard.
- **Co-browsing:** `share(name, view_only=)` returns the invite link; `stop_sharing()`.
- **Local testing:** `open_tunnel(address, client=)`, `tunnel_stats()`, `close_tunnel()`.
- **Proxy:** `own_ip_tunnel()` for the "Your Own IP" kind; `proxy_status()`.
- **iOS:** `devices()`, `set_device(model)`, `set_orientation(degrees)`,
  `press_button(name)`, `show_devtools()`.
- **Apps:** `launch_app()` works on Windows (Explorer, Sysinternals, Command Prompt,
  PowerShell, WSL) as well as Mac; `apps()` lists them.
- **Shell verbs:** `browser`, `os`, `scale`, `clipboard`, `app`, `share`, `tunnel`,
  `device`, `rotate`, `button`, `devtools`, `proxy status`, `screenshot --remote-only`.
- **The shell covers the whole API.** New verbs `dismiss`, `ready`, `status`, `script`,
  `write`, `remove`, `token`, `proxy custom`, `login status`, `capture count`, and new
  options `open --timeout`, `run --timeout`, `download --wait`, `click --double`. A test
  fails if a public `Session` method has no shell command.
- **`bling do "<command>" ...`** runs shell commands from the command line in one session,
  with no prompt and no file.
- **Docs:** quickstart, advanced guide, full API reference, error reference, environments,
  command-line reference, troubleshooting, contributing guide, this changelog.

### Changed

- `open()` ends a session that is live on the page before starting the new one.
- `launch_app()` on a Windows session used to raise; it now opens the app. On iOS and
  Android it raises.
- `navigate(via=...)` raises `BlingError` for anything but `"panel"` or `"remote"`. It used
  to treat every other value as `"panel"`.
- `bling.capture()` and `bling.har()` raise `BlingError` at once for a non-Windows `os`.
- The shell records `capture start` and `capture save`, so a recorded click-through
  replays. They used to be left out of recordings.
- The shell's `type_env` also reads `.env` and `keys.env`, as `bling login` does.
- `config.MENU_ITEMS["share"]` points at Browserling's Co-browsing button; the old Share
  browser button is gone from their page.
- The version is declared once, in `bling/__init__.py`.

### Fixed

- **Wrong machine after reopening.** Calling `open()` while a session was live returned
  `"ready"` on the *old* machine, whatever OS was asked for, because Browserling resumes a
  live session for any new address. Scripts that reopened to change OS were silently
  testing the first OS every time.
- Playwright timeouts in `open()`, `navigate()`, `set_resolution()`, `launch_app()` and
  `is_logged_in()` escaped as Playwright errors. They are now `BlingError`, as the docs
  always said.
- `set_proxy()` can leave the "Your Own IP" screen, which has a different button layout.
- Docs: the error display, the login message, the dependency list and the `--proxy`
  choices matched the code only loosely; the reference is now written from the code.

## 0.1.0

First version: HAR capture (`bling.har`, `capture`, `capture_here`, multi-page capture),
`Session` with proxies, screen sizes, file transfer, remote commands on Windows and Mac,
keyboard and mouse, the `bling` command line, and the recording shell.
