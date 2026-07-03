# bling shell — interactive REPL with record + replay

`bling shell` is an interactive prompt for driving one Browserling session by hand — like
`ipython` for bling. You type verbs one at a time (`open`, `proxy`, `navigate`, `type`,
`key`, `screenshot`), watch each one hit a live VM, and optionally **record** the whole run
to a plain-text `.bling` file. `bling play` replays that file unattended.

```bash
bling shell                                   # just drive a session
bling shell --record login.bling     # …and save it as you go
bling play  login.bling              # replay it later, no prompt, no TTY
```

The shell shows the browser window by default (you want to watch it). Pass `--headless` to
hide it. `bling play` runs headless by default; pass `--live` to watch a replay.

## Command reference

Each verb wraps one `Session` method; defaults match the Session defaults.

| Command | What it does |
|---|---|
| `open <url> [--os win10] [--browser chrome138]` | Open a session and wait until the canvas is ready. Creates the browser (and checks login) on first use. |
| `navigate <url> [--via panel\|remote]` | Load a new URL — via the control panel (default) or the remote browser's own address bar. |
| `proxy <kind> [country]` | Route through a proxy/VPN (`datacenter`/`residential`/`mobile`/`tor`), then wait ready. |
| `resolution <WxH>` | Set the remote screen resolution, e.g. `resolution 1920x1080`. |
| `focus` | Give the VM keyboard focus so OS shortcuts (Win+R) forward into it. |
| `key <combo>` | Press a key/chord, e.g. `key Tab`, `key Control+A`. |
| `type <text>` | Type literal text into the VM. **Recorded in plaintext — never use for secrets.** |
| `type_env <ENVVAR>` | Type the *value* of an env var; records only the variable name (see Secrets). |
| `click <x> <y>` | Click a pixel in the remote view. |
| `run <command>` | Run a shell command in the VM and print its output. |
| `upload <local> [remote_name]` | Push a local file into the VM's Downloads. |
| `download <remote> <out>` | Pull a file out of the VM to the local machine. |
| `screenshot [path]` | Save a PNG of the session (default `shot.png`). |
| `wait <seconds>` | Sleep — an explicit pause you can put in a recording for playback. |
| `end` | End the Browserling VM. The shell stays open; `open <url>` starts a fresh one. |
| `record on <file>` / `record off` | Toggle recording mid-session. |
| `play <file>` | Replay a `.bling` file (works from inside the shell too). |
| `help [cmd]` | Built-in help (reads each verb's docstring). |
| `quit` / `exit` / Ctrl-D | Close the session and exit. Ctrl-C returns to the prompt. |

## Worked example: record a login

Put the credentials in the environment first (bling reads them from `keys.env`/`.env` up the
tree, or you can `setx` them). Then record:

```
$ bling shell --record login.bling
bling 0.1.0 — interactive Browserling driver
type `help` to list commands, `help <cmd>` for usage, `quit` to exit
(recording -> login.bling)

bling> open https://app.example.com/login
ready: https://app.example.com/login on win10/chrome138

bling> proxy mobile "United States"
setting mobile proxy (country: United States) ...
ready

bling> navigate https://app.example.com/login --via remote

bling> type_env APP_EMAIL
(secret typed from env: APP_EMAIL)

bling> key Tab

bling> type_env APP_PASSWORD
(secret typed from env: APP_PASSWORD)

bling> key Enter

bling> screenshot post-login.png
post-login.png

bling> quit
session closed.
```

The resulting `login.bling`:

```
# bling recording — session 2026-07-02T14:23:01
open https://app.example.com/login
proxy mobile "United States"
navigate https://app.example.com/login --via remote
type_env APP_EMAIL
# (secret typed from env: APP_EMAIL)
key Tab
type_env APP_PASSWORD
# (secret typed from env: APP_PASSWORD)
key Enter
screenshot post-login.png
```

Replay it any time (reads the same env vars for the secrets):

```bash
bling play login.bling
```

## Recording format

Plain text, one command per line, `#` starts a comment — hand-editable, diffable, and safe
to paste into a bug report. Playback skips blank lines and comments and runs every other line
through the same dispatch path the REPL uses, so what you record is exactly what replays.
Extension: `.bling`.

## Secrets policy

The recording is meant to be shareable, so **plaintext passwords must never land in it**:

- `type <text>` records the literal text. Fine for `type "hello world"`, wrong for a password.
- `type_env <ENVVAR>` types the *value* of the environment variable into the VM but writes
  only `type_env ENVVAR` to the recording, plus a `# (secret typed from env: …)` marker. The
  replay reads the same variable at playback time, so the file is portable between machines
  that share the variable — and the secret itself is never on disk.
- If the variable is unset, `type_env` fails loudly rather than silently typing nothing into a
  password field.

## Lifecycle notes

- The session is created lazily on the first `open`, and login is checked then — if the cookie
  has expired you'll get `Not logged in — run once: bling login` before anything else happens.
- `end` ends the remote VM but leaves the shell (and browser) up, so `open <url>` starts a
  fresh session without restarting the shell.
- Quitting the shell (or Ctrl-D) always closes the session. So does simply abandoning the
  process — the underlying `Session` registers an `atexit` guard so a forgotten shell can't
  strand a live VM ("too many sessions").

## Errors

Every `BlingError` is caught at the dispatch level and printed as `error: <msg>`. Interactively
you just recover at the prompt; during `play` the run aborts at the first failing line and
reports where it stopped. For example, `run` surfaces its own guidance when a command is too
long or contains double-quotes (Win+R can't route it) instead of failing silently.

## Playback vs. the prompt

`bling shell` needs a real terminal (it reads from stdin). `bling play <file>` does not — it
takes no input, so it runs in any context, including a pipe, cron job, or background process.
Keep those two paths distinct when scripting.
