# The `bling` command line

Every command, every flag, what it prints and what it returns.

**See also:** [Quickstart](QUICKSTART.md) · [Python API](API.md) ·
[`bling shell`](SHELL.md) · [Troubleshooting](TROUBLESHOOTING.md)

```text
bling login
bling har   <url> [--out PATH] [--os OS] [--live] [--proxy KIND] [--country NAME]
bling urls  <file.har> [--summary]
bling open  <url> [--os OS] [--browser BROWSER] [--proxy KIND] [--country NAME] [--live] [-k]
bling run   "<command>" [--os OS] [--browser BROWSER] [--live]
bling shell [--record FILE] [--play FILE] [--headless]
bling play  <file.bling> [--live]
bling --version
```

`python -m bling.cli` is the same program, for a machine where the `bling` script is not on
the path.

## Rules that hold for every command

- **Exit code.** `0` when the command did what it was asked. `1` when bling raised an
  error (not logged in, session refused, a timeout, a failed transfer); the error is shown
  in a red panel that says how to fix it. `2` when the arguments are wrong, with usage text
  (argparse's convention). `bling play` also returns `1` when a line of the recording fails.
- **Data goes to standard output, everything else to standard error.** The URLs from
  `bling urls`, the output of `bling run` and the screenshot path from `bling open` are
  plain text on stdout, so they pipe cleanly. Progress spinners, success lines, tables and
  error panels go to stderr.
- **Headless unless you ask.** Commands run with no visible browser window. `--live` shows
  the window. `bling shell` is the exception: it shows the window unless you pass
  `--headless`, because you are driving by hand.
- **One login.** Every command except `login` and `urls` needs the cookie that
  `bling login` saves. Without it they stop with "Not logged in — run once: bling login".

## `bling login`

Opens a Chrome window on Browserling's sign-in. You solve the reCAPTCHA and press Continue;
bling never solves it for you. The cookie is saved in the profile folder
(`~/.browserling_pw_profile`) and later commands run unattended until it expires.

If `BROWSERLING_EMAIL` and `BROWSERLING_PASSWORD` are set, in the environment or in a
`.env` or `keys.env` file, the form is filled in for you. bling waits 240 seconds for you
to finish, then stops with an error.

```bash
bling login
```

## `bling har <url>`

Records every request a page makes, from a real Firefox inside a Windows session, and
saves it as a `.har` file.

| Flag | Default | Meaning |
|---|---|---|
| `--out PATH` | `<host>.har` | Where to write the file |
| `--os OS` | `win10` | The Windows version to use. Capture is Windows only; any other OS is refused |
| `--live` | off | Show the browser window |
| `--proxy KIND` | none | Capture through a proxy: `datacenter`, `residential`, `mobile` or `tor` |
| `--country NAME` | the kind's default | The proxy's exit country, matched without regard to case |

It prints "saved `<file>`" and a summary table of the capture to stderr.

```bash
bling har demo.browserling.com
bling har suspicious.example --proxy residential --country germany --out de.har
```

A custom proxy (your own SOCKS or HTTP server) is not offered here; use
[`Session.set_proxy("custom", ...)`](API.md#set_proxy) from Python.

## `bling urls <file.har>`

Prints every URL a `.har` file requested, one per line, in request order. It reads the file
locally and needs no login or session, and it works on a HAR saved by any browser.

| Flag | Meaning |
|---|---|
| `--summary` | Also show the summary table (on stderr) |

```bash
bling urls de.har > de.txt
bling urls us.har | sort -u | diff - <(sort -u de.txt)
```

## `bling open <url>`

Opens a session, waits until it is ready, saves a screenshot to `_explore/session.png`
(creating the folder) and prints that path. With `--keep-open` it leaves the session up
for you instead.

| Flag | Default | Meaning |
|---|---|---|
| `--os OS` | `win10` | Operating system: a slug such as `win11`, `mac15`, `ios18`, `android15`, or the menu's name in quotes (`"Windows 11"`). See [Environments](ENVIRONMENTS.md) |
| `--browser BROWSER` | `chrome138` | Browser, with an optional version: `firefox`, `chrome120`, `safari` |
| `--proxy KIND` | none | `datacenter`, `residential`, `mobile` or `tor` |
| `--country NAME` | the kind's default | The proxy's exit country |
| `--live` | off | Show the browser window |
| `-k`, `--keep-open` | off | Leave the session open until you close the browser window or press Ctrl-C. Shows the window, takes no screenshot, needs no terminal input |

```bash
bling open example.com --os "Windows 11" --browser firefox
bling open https://app.example.com/login --keep-open
```

## `bling run "<command>"`

Opens a session, runs one shell command on the remote machine and prints its output
(stdout and stderr together) to stdout.

| Flag | Default | Meaning |
|---|---|---|
| `--os OS` | `win10` | Windows or Mac; phones have no shell |
| `--browser BROWSER` | `chrome138` | The browser the session opens with |
| `--live` | off | Show the browser window |

On Windows the command goes through the Run box, so it must be under about 255 characters
and hold no double quotes; on a Mac it is a zsh line with neither limit. For anything
longer, upload a script from Python and call [`run_script`](API.md#run_script).

```bash
bling run "whoami"
bling run "sw_vers" --os mac15 --browser safari
```

## `bling shell`

An interactive prompt that drives one session a command at a time, and can record what you
type as a `.bling` file. All of its commands are in [SHELL.md](SHELL.md).

| Flag | Meaning |
|---|---|
| `--record FILE` | Append every action you type to `FILE` |
| `--play FILE` | Replay `FILE` first, then give you the prompt |
| `--headless` | Do not show the browser window |

`python -m bling.shell` takes the same flags.

## `bling play <file.bling>`

Replays a recording with no prompt, so it runs from a pipe, a scheduler or a background
job. It stops at the first line that fails and returns `1`; otherwise `0`.

| Flag | Meaning |
|---|---|
| `--live` | Show the browser window during the replay |

```bash
bling play examples/click_through.bling
```

## Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `BROWSERLING_EMAIL`, `BROWSERLING_PASSWORD` | `bling login` | Pre-fill the sign-in form. Also read from `.env` or `keys.env` in the current folder or above |
| `BLING_LIVE` | the test suite | `1` lets the live tests run; see [CONTRIBUTING](../CONTRIBUTING.md) |
| any name you choose | `type_env NAME` in the shell | Its value is typed into the session; only the name is recorded. Also read from `.env` or `keys.env` |
