# Troubleshooting

Symptoms, causes and fixes, most common first. For the meaning of a specific error
message, see [ERRORS.md](ERRORS.md).

- ["Not logged in"](#not-logged-in)
- [A click on the session menu times out](#a-click-on-the-session-menu-times-out)
- [A Browserling window is in my screenshot](#a-browserling-window-is-in-my-screenshot)
- [The page in my screenshot is blank, walled or wrong](#the-page-in-my-screenshot-is-blank-walled-or-wrong)
- ["Too many sessions" or a plan-limit block](#too-many-sessions-or-a-plan-limit-block)
- ["Another host joined"](#another-host-joined)
- [`TargetClosedError` in the middle of a run](#targetclosederror-in-the-middle-of-a-run)
- [The session never becomes ready](#the-session-never-becomes-ready)
- [`run()` hangs, times out or returns nothing](#run-hangs-times-out-or-returns-nothing)
- [`run_script()` returned at once, or `download()` gave me an old file](#run_script-returned-at-once-or-download-gave-me-an-old-file)
- [My keystrokes do nothing](#my-keystrokes-do-nothing)
- [I asked for one OS and got another](#i-asked-for-one-os-and-got-another)
- [`set_proxy()` fails](#set_proxy-fails)
- [`curl` inside the machine ignores my proxy](#curl-inside-the-machine-ignores-my-proxy)
- [The tunnel stays on "Waiting for connection..."](#the-tunnel-stays-on-waiting-for-connection)
- [The capture is empty or times out](#the-capture-is-empty-or-times-out)
- [It fails inside Jupyter](#it-fails-inside-jupyter)
- [`playwright install chrome` wants `--force`](#playwright-install-chrome-wants---force)
- [Asking for help](#asking-for-help)

## "Not logged in"

`NotLoggedIn: Not logged in — run once: bling login`. The cookie in the profile folder is
missing or expired. Run `bling login` and solve the CAPTCHA. Nothing else fixes it: the
sign-in is CAPTCHA-gated on purpose and bling does not solve it.

If you pass `Session(profile=...)`, that folder needs its own sign-in:
`python -c "import bling; bling.login(profile='path')"`, or copy a signed-in profile.

## A click on the session menu times out

`BlingError: menu item '...' not clickable`, `the ... window did not open`, or
`timed out ...; is a session open and the page clear?`

Nearly always a Browserling popup is sitting over the menu and swallowing the click. Call
`s.dismiss()` and try again. Make `s.dismiss()` a habit right after `open()`, when
Browserling's announcements appear.

Other causes: no session is open (you called it before `open()`, or the session ended);
or the button does not exist on this OS (Launch apps on a phone, file transfer on Android).

To see what is in the way, take a full-page shot: `s.screenshot("stuck.png")`.

If a method fails this way on a fresh session with nothing on screen, Browserling has
probably changed its page. The selectors are in `bling/config.py`; see
[CONTRIBUTING](../CONTRIBUTING.md#when-browserling-changes-its-page).

## A Browserling window is in my screenshot

`set_proxy()` and `set_resolution()` leave their windows open, and Escape does not close
them. Call `s.dismiss()` before the shot, or take it with
`s.screenshot(path, remote_only=True)`, which hides Browserling's menu and windows for the
picture.

## The page in my screenshot is blank, walled or wrong

- **Blank or a robot check:** you are probably on a datacenter address, which many sites
  distrust. Use `s.set_proxy("residential", ...)` or `"mobile"`.
- **"Before you continue" consent wall:** not a proxy problem. Click through it with
  `canvas_click` on "Reject all".
- **Only the top of the page:** a screenshot is one screen. `s.key("PageDown")` and shoot
  again.
- **Text too small to read:** set `s.set_resolution("1280x1024")`. Larger remote screens
  are scaled down to fit the canvas.
- **Still the previous page:** `navigate()` returns after 0.8 seconds, not when the page
  has loaded. Wait before the shot: `s.page.wait_for_timeout(5000)`.

## "Too many sessions" or a plan-limit block

`SessionBlocked: session blocked: ... limit reached`. Your plan allows a fixed number of
sessions at once and a machine from an earlier run is still alive.

- Always use `with bling.Session() as s:`. It ends the session even when your code raises.
- A run killed hard (the terminal closed, the process killed) cannot clean up. Its machine
  ends by itself after Browserling's idle timeout, about ten minutes.
- Sign in at browserling.com to see and end live sessions by hand.

## "Another host joined"

`SessionBlocked: ... another host joined (duplicate session)`. The same Browserling account
opened the same session from somewhere else: a second person on a shared account, or your
own second window. Use one account per person. For your own parallel runs, give each its
own profile folder ([Advanced](ADVANCED.md#parallel-sessions)).

## `TargetClosedError` in the middle of a run

`TargetClosedError: Target page, context or browser has been closed`, often repeated for
every remaining step. Two bling processes are using the same Chrome profile folder and the
second one closed the first one's browser. One run per profile:
[Advanced](ADVANCED.md#parallel-sessions).

It also follows closing the Chrome window by hand during a `headless=False` run.

## The session never becomes ready

`NotReady: session did not become ready in time`.

- Phones and Macs take longer than Windows. Pass `ready_timeout=120` or more.
- Browserling may be short of that kind of machine. Try again, or another version.
- If it happens on every OS, check that your network allows Browserling's streaming
  connection; a blocked one usually shows as `SessionBlocked: ... firewall`.

## `run()` hangs, times out or returns nothing

- **The command starts a program** (a browser, an installer, anything that stays open):
  `run()` waits on the program. Use `run_script()` with a sentinel file.
- **It needs double quotes or is long** (Windows): `run()` refuses these. Upload a `.bat`
  or `.py` and run it by path.
- **Keys went elsewhere:** a Browserling window had the focus. Call `s.dismiss()` before
  `run()`.
- **It is a phone:** there is no shell on iOS or Android.
- **Empty output:** the command printed nothing. `run()` returns standard output and
  standard error together, so an error would show.

## `run_script()` returned at once, or `download()` gave me an old file

Browserling sometimes hands out a machine an earlier session used, with its Downloads
folder intact. `run_script()` deletes its own sentinel first, but other files are yours:
call `s.remove("result.txt")` before the run, or put a unique tag in each run's file names.
See [Advanced](ADVANCED.md#a-machine-can-arrive-with-old-files).

## My keystrokes do nothing

The remote screen does not have the keyboard. `s.focus_vm()` gives it back; `s.dismiss()`
does that and closes whatever took it. On a Mac the first key after focusing is dropped,
which `run()` allows for; if you type by hand with `type()`, send a harmless key first.

## I asked for one OS and got another

Before version 0.2.0, calling `open()` while a session was live got the old machine back
with no error. Upgrade. From 0.2.0, `open()` and `set_os()` end the live session first.
To check what you are on, look at `s.page.url`: it reads `/browse/<os>/<browser>...`.

## `set_proxy()` fails

- **`no <kind> proxy location matches ...`:** that kind does not have the country. The
  message lists the kinds that do.
- **`Browserling refused set_proxy(...)`:** Browserling's own refusal, after one retry. It
  happens for particular exits on particular days. Try another kind or country; the session
  keeps the exit it had.
- **`stalled while ...`:** the panel stopped responding. `s.dismiss()` and call it again.

Mobile exits use a monthly data allowance shown in Browserling's proxy window. When it runs
out, mobile stops connecting.

## `curl` inside the machine ignores my proxy

That is how Browserling's proxies work: they apply to the machine's browsers, not to its
command-line tools. Read the proxy address from the registry and pass it to the tool. The
working script is in [Advanced](ADVANCED.md#the-network-exit-inside-the-machine).

## The tunnel stays on "Waiting for connection..."

The SSH command from `open_tunnel()` is not running, or could not connect.

- Run it on a machine that can reach the address you named, and leave it running. Add
  `-N` (no remote shell) and, in scripts, `-o StrictHostKeyChecking=accept-new`.
- Outbound SSH (port 22) to `tunnel.browserling.com` must be allowed by your network.
- On OpenSSH older than 7.6 use `open_tunnel(address, client="old-ssh")`.
- "Waiting for server..." means SSH connected and your web server did not answer. Check
  that it is listening on that address.
- The command is good for the session that issued it. A new session needs a new command.

## The capture is empty or times out

`Timeout: '....har' did not appear/settle within Ns`, or a HAR with no entries.

- Raise the wait: `bling.har(url, timeout=120)`. The HAR is written when the page finishes
  loading; a page that never stops loading never writes one.
- Watch it: `bling.har(url, live=True)`.
- The page may be refusing the machine's address. Capture through a proxy:
  `bling har <url> --proxy residential --country germany`.
- Capture is Firefox on Windows 10 or 11. A page that hides from Firefox will hide here;
  compare with screenshots in other browsers ([examples](../examples/README.md)).

## It fails inside Jupyter

bling uses Playwright's synchronous mode, which cannot start inside a running event loop,
and a notebook has one. Run bling from a script, from plain `python`, or from IPython in a
terminal. From a notebook, shell out: `!bling har example.com`.

## `playwright install chrome` wants `--force`

When Chrome is already installed, the command says so and suggests re-running with
`--force`. Do not: that starts by uninstalling your Chrome. The message means you are
already set up.

## Asking for help

Include, in an issue on the repository:

1. `bling --version`, your OS, and `python --version`.
2. The smallest script or `.bling` recording that shows the problem.
3. The full error message. bling's errors carry the session menu's state and Playwright's
   call log, which is usually enough to find the cause.
4. A full-page `s.screenshot("stuck.png")` taken at the failure.

Remove secrets first: tunnel commands, invite links, transfer links, cookies in a HAR. See
[Advanced](ADVANCED.md#secrets).
