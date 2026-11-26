"""bling shell — an interactive REPL that drives one Browserling Session, with record + replay.

Think ``ipython`` for bling: you type verbs (``open``, ``proxy``, ``navigate``, ``type``,
``key``, ``screenshot`` …) one at a time and watch each hit a live VM, then save the whole
run as a hand-editable ``.bling`` file you can replay unattended.

Two entry points, deliberately distinct:
  * ``BlingShell().cmdloop()`` — the interactive REPL. Needs a real TTY (it calls ``input()``).
  * ``run_shell(play=..., exit_after_play=True)`` — replay a recording with no prompt. Runs
    anywhere, including a pipe or a background job — no TTY required.

Each verb is a thin wrapper over one ``Session`` method (see the table in
``docs/SHELL.md``). Every ``BlingError`` is caught at the dispatch level and printed as
``error: <msg>`` — an interactive user just recovers at the prompt; a playback aborts.

Secrets never land in a recording: ``type <text>`` records the literal text (fine for
non-secret input), while ``type_env ENVVAR`` types the *value* of an environment variable
into the VM but records only the variable name.
"""

from __future__ import annotations

import cmd
import os
import re
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from dotenv import find_dotenv, load_dotenv

from . import __version__, config, ui
from .errors import BlingError
from .har import HAR, arm_capture, capture_count, capture_here, sweep_captures
from .session import Session, login

# Meta-verbs that control the shell itself — never written to a recording (they don't
# reconstruct a session, and replaying them would be surprising, recursive, or — for login —
# pop a headed CAPTCHA window mid-playback). `capture` is an action and is recorded: a
# click-through recording is useless on replay without its `capture start` and `capture save`.
_NO_RECORD = frozenset({"login", "record", "play", "quit", "exit", "EOF", "help", "?"})

# Proxy kinds Browserling offers, for `help proxy` and tab completion.
_PROXY_KINDS = ("datacenter", "residential", "mobile", "tor")
_TUNNEL_SUBS = ("open", "stats", "close", "own-ip")

# The end-of-input key differs by platform — Ctrl-D signals EOF on Unix, Ctrl-Z + Enter on
# Windows. Used in the "how to exit" hints so we never tell a Windows user to press Ctrl-D.
_EOF_HINT = "Ctrl-Z then Enter" if os.name == "nt" else "Ctrl-D"

# Order verbs appear in the `help` table (grouped by workflow, not alphabetical).
_HELP_ORDER = (
    "login",
    "open",
    "ready",
    "status",
    "navigate",
    "browser",
    "os",
    "proxy",
    "resolution",
    "scale",
    "dismiss",
    "focus",
    "key",
    "type",
    "type_env",
    "click",
    "clipboard",
    "run",
    "script",
    "app",
    "upload",
    "write",
    "download",
    "remove",
    "token",
    "screenshot",
    "har",
    "urls",
    "capture",
    "share",
    "tunnel",
    "device",
    "rotate",
    "button",
    "devtools",
    "wait",
    "end",
    "record",
    "play",
    "quit",
)


def _lex(arg: str) -> list[str]:
    """Split a command's argument string into tokens, Windows-path-safe.

    Plain ``shlex.split`` treats ``\\`` as an escape, which mangles ``C:\\dir\\file`` — and
    bling is Windows-first. So we split in non-POSIX mode (backslashes survive) and then
    strip any surrounding quotes ourselves, so ``proxy mobile "United States"`` still works.

    >>> _lex('mobile "United States"')
    ['mobile', 'United States']
    >>> _lex(r'C:\\src\\login.bling')
    ['C:\\\\src\\\\login.bling']
    """
    out: list[str] = []
    for tok in shlex.split(arg, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _split_opts(tokens: list[str], flags: set[str]) -> tuple[list[str], dict[str, str]]:
    """Split shlex tokens into positionals and ``--flag value`` pairs.

    >>> _split_opts(["ex.com", "--os", "win11"], {"--os", "--browser"})
    (['ex.com'], {'--os': 'win11'})
    """
    positional: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in flags:
            opts[tok] = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
        else:
            positional.append(tok)
            i += 1
    return positional, opts


def _seconds(text: str, what: str) -> int:
    """Parse a whole number of seconds for ``what``, or raise a BlingError that says so.

    >>> _seconds("90", "ready")
    90
    """
    try:
        value = int(text.strip())
    except ValueError:
        raise BlingError(f"{what} needs a whole number of seconds, not {text!r}") from None
    if value <= 0:
        raise BlingError(f"{what} needs a number of seconds above zero, not {value}")
    return value


class BlingShell(cmd.Cmd):
    """Interactive Browserling driver. One shell drives one Session at a time.

    >>> sh = BlingShell(headless=False)
    >>> sh.prompt
    'bling> '

    Then either dispatch single lines (what playback uses) or run the interactive prompt::

        sh.onecmd('open example.com')   # runs one command against a live VM
        sh.cmdloop()                    # interactive prompt (needs a real TTY)
    """

    prompt = "bling> "

    def __init__(self, *, headless: bool = False):
        super().__init__()
        self._headless = headless
        self._session: Session | None = None
        self._rec: TextIO | None = None
        self._rec_path: Path | None = None
        # Set by onecmd/default on failure so a playback loop can abort on first error.
        self._error: BaseException | None = None
        # Capture mode (record a HAR per page): None, or {"bounds": [(file_index, proxy_label)]}.
        self._cap: dict | None = None
        self._proxy_label = "noproxy"
        self.prompt = ui.styled_prompt(recording=False)

    def _refresh_prompt(self) -> None:
        """Keep the prompt's REC marker in sync with whether we're recording."""
        self.prompt = ui.styled_prompt(recording=self._rec is not None)

    # --- session lifecycle --------------------------------------------------
    def _ensure_session(self) -> Session:
        """Return the live Session, creating (and login-checking) it on first use."""
        if self._session is None:
            s = Session(headless=self._headless).start()
            try:
                s.require_login()
            except BlingError:
                s.close()  # don't leave a browser up if we're not even logged in
                raise
            self._session = s
        return self._session

    def _new_machine(self) -> None:
        """Forget what belonged to the last machine: its proxy and any armed capture."""
        self._proxy_label = "noproxy"
        self._cap = None

    def _need_session(self) -> Session:
        """Return the live Session, or raise with the fix if `open` hasn't run yet."""
        if self._session is None:
            raise BlingError("no session yet — run `open <url>` first")
        return self._session

    def _shutdown(self) -> None:
        """Close the recording and the Session. Safe to call more than once."""
        self._close_recording()
        if self._session is not None:
            self._session.close()
            self._session = None

    # --- recording ----------------------------------------------------------
    def _open_recording(self, path) -> None:
        """Start (or switch) recording typed commands to ``path`` (appends)."""
        self._close_recording()
        self._rec_path = Path(path)
        self._rec = open(self._rec_path, "a", encoding="utf-8")
        stamp = datetime.now().isoformat(timespec="seconds")
        self._rec.write(f"# bling recording — session {stamp}\n")
        self._rec.flush()
        self._refresh_prompt()

    def _close_recording(self) -> None:
        if self._rec is not None:
            self._rec.close()
            self._rec = None
            self._rec_path = None
        self._refresh_prompt()

    def _record_comment(self, text: str) -> None:
        """Write a ``# comment`` line into the active recording (no-op if not recording)."""
        if self._rec is not None:
            self._rec.write(f"# {text}\n")
            self._rec.flush()

    # --- dispatch (recording + centralized error handling) ------------------
    def precmd(self, line: str) -> str:
        """Record each interactively-typed action verb verbatim (the recording hook).

        Playback goes through ``onecmd`` directly and bypasses this, so replaying a file
        never re-records it. Meta-verbs (``record``, ``play``, ``quit`` …) are skipped.
        """
        stripped = line.strip()
        verb = stripped.split(" ", 1)[0] if stripped else ""
        if self._rec is not None and stripped and verb not in _NO_RECORD:
            self._rec.write(stripped + "\n")
            self._rec.flush()
        return line

    def onecmd(self, line: str):
        """Dispatch one line, catching every BlingError so the loop never crashes.

        On a bling error we print ``error: <msg>`` and stash it in ``self._error`` — the
        interactive loop just carries on at the prompt; ``do_play`` checks ``self._error``
        and aborts the playback. Unexpected exceptions are shown but likewise don't crash
        an interactive session.
        """
        self._error = None
        try:
            return super().onecmd(line)
        except BlingError as e:
            self._error = e
            ui.error_panel(e)
            return False
        except Exception as e:  # noqa: BLE001 — interactive: show it, stay at the prompt
            self._error = e
            ui.error_panel(f"unexpected {type(e).__name__}: {e}")
            return False

    def emptyline(self) -> bool:
        """Do nothing on a blank line (cmd.Cmd's default re-runs the last command)."""
        return False

    def default(self, line: str):
        """Unknown verb — report it and mark an error so a playback aborts here too.

        A lone EOF keystroke lands here rather than raising EOFError: on Windows, Ctrl-D isn't
        end-of-input, it just echoes ``\\x04`` into the line. We read that as "exit", so the
        habit works on every platform.
        """
        stripped = line.strip().strip("\x04\x1a")  # drop a stray Ctrl-D (\x04) / Ctrl-Z (\x1a)
        if not stripped:
            return self.do_quit("")  # a typed EOF key means "quit"
        self._error = BlingError(f"unknown command: {stripped}")
        ui.error_panel(self._error)
        return None

    def do_help(self, arg: str) -> None:
        """help [command] — list every command, or show detailed usage for one."""
        if arg:
            super().do_help(arg)
            return
        rows = []
        for verb in _HELP_ORDER:
            doc = (getattr(self, f"do_{verb}").__doc__ or "").strip()
            first = doc.splitlines()[0] if doc else ""
            desc = first.split(" — ", 1)[1] if " — " in first else first
            rows.append((verb, desc))
        ui.command_help(rows)

    def cmdloop(self, intro=None) -> None:
        """Interactive prompt. Ctrl-C returns to the prompt; an EOF key or `quit` exits."""
        while True:
            try:
                super().cmdloop(intro=intro)
                break
            except KeyboardInterrupt:
                ui.info(f"\n^C  (type `quit` or press {_EOF_HINT} to exit)")
                intro = ""  # don't reprint the banner after the first loop

    # --- verbs: auth ---------------------------------------------------------
    def do_login(self, arg: str) -> None:
        """login | login status — solve the one-time CAPTCHA login, or report whether it holds.

        Opens a headed Chrome window on the shared profile; you solve the CAPTCHA, the cookie
        is saved, and later `open` calls are unattended. Must run before any session is open.
        """
        if arg.strip() == "status":
            if self._session is not None:
                print("logged in")  # the shell only holds a session once the login check passed
                return
            with Session(headless=True) as probe:
                ok = probe.is_logged_in()
            print("logged in" if ok else "not logged in — run `login`")
            return
        if self._session is not None:
            raise BlingError(
                "log in before opening a session — a live session holds the browser profile; "
                "quit and restart the shell, then `login`"
            )
        ui.info("opening a login window — solve the CAPTCHA there, then come back here …")
        login()  # headed Chrome on the shared profile; blocks until solved (or times out)
        ui.success("logged in — the cookie is saved; `open <url>` will work now")

    # --- verbs: session ------------------------------------------------------
    def do_open(self, arg: str) -> None:
        """open <url> [--os win10] [--browser chrome138] [--timeout 45] — open a session.

        Starts a fresh machine and waits until it is ready; `--timeout` is how many seconds
        to wait (phones and Macs want 120). Creates the browser (and checks login) on first
        use. e.g. `open example.com`.
        """
        pos, opts = _split_opts(_lex(arg), {"--os", "--browser", "--timeout"})
        if not pos:
            print("usage: open <url> [--os win10] [--browser chrome138] [--timeout 45]")
            return
        kwargs = {}
        if "--timeout" in opts:
            kwargs["ready_timeout"] = _seconds(opts["--timeout"], "open --timeout")
        if "--os" in opts:
            kwargs["os"] = opts["--os"]
        if "--browser" in opts:
            kwargs["browser"] = opts["--browser"]
        os_ = kwargs.get("os", "win10")
        browser = kwargs.get("browser", "chrome138")
        s = self._ensure_session()
        with ui.status(f"opening {pos[0]} on {os_}/{browser} …"):
            state = s.open(pos[0], **kwargs)
        self._new_machine()
        ui.success(f"{state}: {pos[0]} on {os_}/{browser}")

    def do_ready(self, arg: str) -> None:
        """ready [seconds] — wait until the remote screen is streaming (default 45 seconds)."""
        timeout = _seconds(arg, "ready") if arg.strip() else 45
        ui.success(self._need_session().wait_ready(timeout=timeout))

    def do_status(self, arg: str) -> None:
        """status — show the session's OS, browser, address and proxy."""
        s = self._need_session()
        now = s._menu_lists()
        print(f"os:      {now['platform']} ({s.os}, family {s.platform})")
        print(f"browser: {now['browser']}")
        if now["device"]:
            print(f"device:  {now['device']}")
        print(f"address: {s.page.url}")
        print(f"proxy:   {s.proxy_status()['status']}")

    def do_navigate(self, arg: str) -> None:
        """navigate <url> [--via panel|remote] — load a new URL in the running session.

        via=panel (default) uses the control-panel field; via=remote drives the remote
        browser's own address bar (keeps its DevTools open).
        """
        pos, opts = _split_opts(_lex(arg), {"--via"})
        if not pos:
            print("usage: navigate <url> [--via panel|remote]")
            return
        s = self._need_session()
        if self._cap is not None:
            # Capture mode drives the instrumented Firefox, whose address bar isn't where the
            # panel/streamed layout expects — so navigate it by keyboard.
            s.focus_vm()
            s.key("Control+l")
            s.page.wait_for_timeout(400)
            s.type(pos[0])
            s.key("Enter")
            s.page.wait_for_timeout(800)
            return
        s.navigate(pos[0], via=opts.get("--via", "panel"))

    def do_browser(self, arg: str) -> None:
        """browser [name] [version] — switch browser on the same machine; no name lists them.

        The machine, its files and its proxy are kept. e.g. `browser Firefox`, `browser Chrome 120`.
        """
        toks = _lex(arg)
        s = self._need_session()
        if not toks:
            for name, versions in s.browsers().items():
                print(f"{name}: {', '.join(versions[:6])}{' ...' if len(versions) > 6 else ''}")
            return
        # A last word that starts with a digit is the version; the rest is the name, so
        # `browser Tor Browser 16` and `browser Internet Explorer` need no quotes.
        version = toks.pop() if len(toks) > 1 and toks[-1][:1].isdigit() else None
        with ui.status(f"switching to {' '.join(toks)}{' ' + version if version else ''} …"):
            state = s.set_browser(" ".join(toks), version)
        toks = toks + ([version] if version else [])
        ui.success(f"{state}: {' '.join(toks)}")

    def do_os(self, arg: str) -> None:
        """os [name] [--browser b] — move to another OS on a new machine; no name lists them.

        A new machine starts, so files and the proxy do not carry over. e.g. `os "Windows 11"`,
        `os mac15 --browser firefox`.
        """
        pos, opts = _split_opts(_lex(arg), {"--browser"})
        s = self._need_session()
        if not pos:
            print("\n".join(s.platforms()))
            return
        name = " ".join(pos)
        with ui.status(f"opening a {name} machine …"):
            state = s.set_os(name, browser=opts.get("--browser"))
        self._new_machine()
        ui.success(f"{state}: {config.os_slug(name)}")

    def do_proxy(self, arg: str) -> None:
        """proxy <kind> [country] | proxy custom <host:port> | proxy status — set the exit.

        kind is one of datacenter | residential | mobile | tor. e.g. `proxy mobile "United States"`.
        `proxy custom proxy.example.org:1080 --user me --password-env PROXY_PASS` uses your own
        SOCKS or HTTP proxy (`--protocol SOCKS5|SOCKS4|HTTP`); the password comes from an env
        var so it never lands in a recording. `proxy status` prints the exit in use now.
        """
        toks = _lex(arg)
        if not toks:
            kinds = "|".join(_PROXY_KINDS)
            print(f"usage: proxy <{kinds}> [country] | proxy custom ... | proxy status")
            return
        if toks[0] == "status":
            print(self._need_session().proxy_status()["status"])
            return
        if toks[0] == "custom":
            self._proxy_custom(toks[1:])
            return
        kind = toks[0]
        country = toks[1] if len(toks) > 1 else None
        s = self._need_session()
        ui.info(f"setting {kind} proxy" + (f" (country: {country})" if country else "") + " ...")
        with ui.status("routing and waiting for the session to come back …"):
            s.set_proxy(kind, country=country)
            state = s.wait_ready(timeout=45)  # re-routing restarts the VM stream
        ui.success(state)
        self._proxy_label = (f"{kind}-{country}" if country else kind).replace(" ", "-")
        if self._cap is not None:
            # Mark that pages captured from here on were served through this proxy.
            self._cap["bounds"].append((capture_count(s), self._proxy_label))

    def _proxy_custom(self, toks: list[str]) -> None:
        """`proxy custom <host:port> [--protocol P] [--user U] [--password-env VAR]`."""
        pos, opts = _split_opts(toks, {"--protocol", "--user", "--password-env"})
        if not pos:
            print(
                "usage: proxy custom <host:port> [--protocol SOCKS5|SOCKS4|HTTP] "
                "[--user NAME] [--password-env ENVVAR]"
            )
            return
        password = None
        if "--password-env" in opts:
            password = self._env(opts["--password-env"], "proxy custom --password-env")
        s = self._need_session()
        with ui.status(f"routing through your proxy at {pos[0]} …"):
            s.set_proxy(
                "custom",
                address=pos[0],
                username=opts.get("--user"),
                password=password,
                protocol=opts.get("--protocol", "SOCKS5"),
            )
            state = s.wait_ready(timeout=45)
        ui.success(state)
        self._proxy_label = "custom"

    def _env(self, var: str, what: str) -> str:
        """The value of env var ``var``, also looked for in .env and keys.env; raise if unset."""
        if os.getenv(var) is None:
            # The same files `bling login` reads, so a recording's secrets can live in .env.
            load_dotenv(find_dotenv("keys.env", usecwd=True))
            load_dotenv(find_dotenv(usecwd=True))
        value = os.getenv(var)
        if value is None:
            raise BlingError(
                f"env var {var} is not set — export it, or put it in .env or keys.env, "
                f"before `{what}`"
            )
        return value

    def do_resolution(self, arg: str) -> None:
        """resolution <WxH> — set the remote screen resolution, e.g. `resolution 1920x1080`."""
        value = arg.strip()
        if not value:
            print("usage: resolution <WxH>")
            return
        self._need_session().set_resolution(value)

    def do_scale(self, arg: str) -> None:
        """scale <0-100> — magnify the remote screen inside the page (0 fits it): `scale 40`."""
        try:
            percent = int(arg.strip())
        except ValueError:
            raise BlingError("scale needs a whole number from 0 to 100: scale <0-100>") from None
        self._need_session().set_scale(percent)

    def do_dismiss(self, arg: str) -> None:
        """dismiss — close every Browserling window and popup; hand the keyboard to the VM.

        Use it after `open`, `proxy` and `resolution`, whose windows otherwise sit in
        screenshots and swallow clicks.
        """
        self._need_session().dismiss()

    def do_focus(self, arg: str) -> None:
        """focus — give the VM keyboard focus so OS shortcuts (Win+R) forward into it."""
        self._need_session().focus_vm()

    # --- verbs: input --------------------------------------------------------
    def do_key(self, arg: str) -> None:
        """key <combo> — press a key/chord in the focused VM, e.g. `key Tab` or `key Control+A`."""
        combo = arg.strip()
        if not combo:
            print("usage: key <combo>")
            return
        self._need_session().key(combo)

    def do_type(self, arg: str) -> None:
        """type <text> — type literal text into the focused VM. RECORDED IN PLAINTEXT.

        Use `type_env ENVVAR` for anything secret — that keeps the value out of the recording.
        """
        self._need_session().type(arg)

    def do_type_env(self, arg: str) -> None:
        """type_env <ENVVAR> — type the value of an env var into the VM; records only the name.

        Fails loudly if the variable is unset (better than silently typing nothing into a
        password field). e.g. `type_env APP_PASSWORD`.
        """
        var = arg.strip()
        if not var:
            print("usage: type_env <ENVVAR>")
            return
        value = self._env(var, f"type_env {var}")
        self._need_session().type(value)
        ui.info(f"(secret typed from env: {var})")
        self._record_comment(f"(secret typed from env: {var})")

    def do_click(self, arg: str) -> None:
        """click <x> <y> [--double] — click a pixel in the remote view, e.g. `click 450 103`."""
        toks = _lex(arg)
        double = "--double" in toks
        toks = [tok for tok in toks if tok != "--double"]
        if len(toks) != 2:
            print("usage: click <x> <y> [--double]")
            return
        try:
            x, y = int(toks[0]), int(toks[1])
        except ValueError:
            raise BlingError("click needs two integer pixel coordinates: click <x> <y>") from None
        self._need_session().canvas_click(x, y, clicks=2 if double else 1)

    def do_clipboard(self, arg: str) -> None:
        """clipboard get | clipboard set <text> — read or set the remote machine's clipboard.

        `clipboard set` is RECORDED IN PLAINTEXT, like `type`. Paste with `key Control+V`.
        """
        sub, _, rest = arg.strip().partition(" ")
        s = self._need_session()
        if sub == "get":
            print(s.get_clipboard())
        elif sub == "set" and rest:
            s.set_clipboard(rest)
        else:
            print("usage: clipboard get | clipboard set <text>")

    # --- verbs: VM shell + files --------------------------------------------
    def do_app(self, arg: str) -> None:
        """app [name] — open an app from Launch apps, e.g. `app PowerShell`; no name lists them."""
        name = arg.strip().strip("\"'")
        s = self._need_session()
        if not name:
            print("\n".join(s.apps()))
            return
        s.launch_app(name)

    def do_run(self, arg: str) -> None:
        """run [--timeout 60] <command> — run a shell command in the VM and print its output.

        Keep it short (Win+R caps ~255 chars, no embedded double-quotes); for more, `write`
        or `upload` a script and start it with `script`. e.g. `run whoami`,
        `run --timeout 180 ping -n 100 example.com`.
        """
        cmd_str = arg.strip()
        kwargs = {}
        m = re.match(r"--timeout\s+(\S+)\s+(.*)", cmd_str, re.S)
        if m:
            kwargs["timeout"] = _seconds(m.group(1), "run --timeout")
            cmd_str = m.group(2).strip()
        if not cmd_str:
            print("usage: run [--timeout 60] <command>")
            return
        print(self._need_session().run(cmd_str, **kwargs))

    def do_script(self, arg: str) -> None:
        """script <remote_name> <sentinel> [--timeout 90] — start an uploaded script and wait.

        For scripts that open programs or run long, where `run` would hang. The script must
        create a non-empty file named <sentinel> in Downloads when it has finished.
        e.g. `script job.bat job.done --timeout 300`.
        """
        pos, opts = _split_opts(_lex(arg), {"--timeout"})
        if len(pos) != 2:
            print("usage: script <remote_name> <sentinel> [--timeout 90]")
            return
        kwargs = {}
        if "--timeout" in opts:
            kwargs["timeout"] = _seconds(opts["--timeout"], "script --timeout")
        with ui.status(f"running {pos[0]} until it writes {pos[1]} …"):
            self._need_session().run_script(pos[0], sentinel=pos[1], **kwargs)
        ui.success(f"{pos[0]} finished ({pos[1]} written)")

    def do_upload(self, arg: str) -> None:
        """upload <local_path> [remote_name] — push a local file into the VM's Downloads."""
        toks = _lex(arg)
        if not toks:
            print("usage: upload <local_path> [remote_name]")
            return
        remote = toks[1] if len(toks) > 1 else None
        name = self._need_session().upload(toks[0], remote)
        print(f"uploaded -> {name}")

    def do_write(self, arg: str) -> None:
        """write <remote_name> <text> — write one line of <text> as a file in the VM's Downloads.

        For a one-line script or a config; chain commands in a .bat with `&`. For anything
        longer, `upload` a local file. RECORDED IN PLAINTEXT.
        e.g. `write note.txt checked by bling`, then `run type C:/Users/user/Downloads/note.txt`.
        """
        name, _, text = arg.strip().partition(" ")
        if not name or not text:
            print("usage: write <remote_name> <text>")
            return
        saved = self._need_session().upload_text(text + chr(10), name)
        print(f"wrote -> {saved}")

    def do_download(self, arg: str) -> None:
        """download <remote_name> <out_path> [--wait SECONDS] — pull a file out of the VM.

        `--wait` is for a file the VM is still writing: it waits up to that many seconds for
        the file to appear and stop growing.
        """
        pos, opts = _split_opts(_lex(arg), {"--wait"})
        if len(pos) != 2:
            print("usage: download <remote_name> <out_path> [--wait SECONDS]")
            return
        s = self._need_session()
        if "--wait" in opts:
            wait = _seconds(opts["--wait"], "download --wait")
            out = s.download_when_ready(pos[0], pos[1], timeout=wait)
        else:
            out = s.download(pos[0], pos[1])
        print(str(out))

    def do_remove(self, arg: str) -> None:
        """remove <remote_name>... — delete files from the VM's Downloads and confirm they went."""
        names = _lex(arg)
        if not names:
            print("usage: remove <remote_name>...")
            return
        self._need_session().remove(*names)
        ui.info(f"removed {', '.join(names)}")

    def do_token(self, arg: str) -> None:
        """token download | token upload — print the session's file-transfer server and token.

        Files are at https://<server>/<token>/<name>: GET to download, PUT to upload. The
        token is a secret for the life of the session.
        """
        sub = arg.strip()
        s = self._need_session()
        if sub == "download":
            print(" ".join(s.transfer_token()))
        elif sub == "upload":
            print(" ".join(s.upload_token()))
        else:
            print("usage: token download | token upload")

    def do_screenshot(self, arg: str) -> None:
        """screenshot [path] [--remote-only] — save a PNG of the session (default: shot.png).

        `--remote-only` leaves out the session menu and any Browserling window.
        """
        toks = _lex(arg)
        remote_only = "--remote-only" in toks
        toks = [tok for tok in toks if tok != "--remote-only"]
        path = toks[0] if toks else "shot.png"
        out = self._need_session().screenshot(path, remote_only=remote_only)
        print(str(out))

    # --- verbs: HAR capture ---------------------------------------------------
    def do_har(self, arg: str) -> None:
        """har <url> [out.har] — capture the URL's HAR in the current session (keeps any proxy).

        Runs an instrumented Firefox inside the VM you already have, so a proxy set with
        `proxy` still applies — set a country, then `har <url>`, to record the page as served
        to that country. Writes <host>.har (or `out.har`) locally and shows a summary.
        """
        toks = _lex(arg)
        if not toks:
            print("usage: har <url> [out.har]")
            return
        url = toks[0]
        # _host lives in cli (which imports this module) — import lazily to avoid the cycle.
        from .cli import _host

        dest = toks[1] if len(toks) > 1 else _host(url) + ".har"
        s = self._ensure_session()
        with ui.status(f"capturing {url} (instrumented Firefox, current proxy) …"):
            h = capture_here(s, url)
        h.save(dest)
        ui.success(f"saved {dest}")
        ui.har_summary(h)

    def do_urls(self, arg: str) -> None:
        """urls <file.har> — print every URL a .har file requested, one per line (local read)."""
        toks = _lex(arg)
        if not toks:
            print("usage: urls <file.har>")
            return
        for u in HAR.load(toks[0]).urls():
            print(u)

    def do_capture(self, arg: str) -> None:
        """capture start | capture count | capture save <dir> — record a HAR for every page.

        `capture start` arms an instrumented Firefox; from then on every page you load — by
        `navigate`, or by clicking through with `click`/`key` — writes its own HAR, and it keeps
        recording across proxy switches. Use it for malware you have to click through to reach.
        `capture save <dir>` downloads them all, named by order, host, and the active proxy.
        """
        toks = _lex(arg)
        sub = toks[0] if toks else ""
        if sub == "start":
            s = self._ensure_session()
            with ui.status("arming capture — every page you load will be recorded …"):
                arm_capture(s)
            self._cap = {"bounds": [(0, self._proxy_label)]}
            ui.success("recording — navigate/click through, then `capture save <dir>`")
        elif sub == "save":
            if self._cap is None:
                raise BlingError("not recording — run `capture start` first")
            out_dir = Path(toks[1]) if len(toks) > 1 else Path(".")
            out_dir.mkdir(parents=True, exist_ok=True)
            with ui.status("collecting captured HARs from the VM …"):
                hars = sweep_captures(self._need_session())
            manifest = self._save_captures(hars, out_dir)
            self._cap = None
            ui.success(f"saved {len(hars)} HAR(s) to {out_dir}")
            for line in manifest:
                ui.info(line)
        elif sub == "count":
            print(capture_count(self._need_session()))
        else:
            print("usage: capture start | capture count | capture save <dir>")

    def _save_captures(self, hars: list, out_dir: Path) -> list[str]:
        """Write each captured HAR named `NN-<host>-<proxy>.har`, tagging by the proxy that was
        active when it was recorded (from the capture-mode boundaries). Returns a manifest."""
        from .cli import _host

        bounds = self._cap["bounds"]  # [(start_index, proxy_label)], ascending by start_index
        manifest = []
        for i, h in enumerate(hars):
            label = next((lab for start, lab in reversed(bounds) if i >= start), "noproxy")
            name = f"{i:02d}-{_host(h.url)}-{label}.har"
            h.save(Path(out_dir) / name)
            manifest.append(f"{name}  <-  {h.url}  ({len(h)} requests)")
        return manifest

    # --- verbs: sharing and tunnels --------------------------------------------
    def do_share(self, arg: str) -> None:
        """share [name] [--view-only] | share stop — start co-browsing and print the invite link.

        Anyone with the link can join the live session. `share stop` kills the link and
        removes every guest. e.g. `share Analyst --view-only`.
        """
        toks = _lex(arg)
        s = self._need_session()
        if toks and toks[0] == "stop":
            s.stop_sharing()
            ui.info("co-browsing stopped; the invite link no longer works")
            return
        view_only = "--view-only" in toks
        names = [tok for tok in toks if tok != "--view-only"]
        print(s.share(names[0] if names else "bling", view_only=view_only))

    def do_tunnel(self, arg: str) -> None:
        """tunnel open <address> [ssh|old-ssh|putty] | stats | close | own-ip — local testing.

        `tunnel open localhost:8080` prints the SSH command to run on the machine that can
        reach that address; the session can then open it. `tunnel own-ip` prints the command
        that routes the whole session through your own connection. Both carry a secret.
        """
        toks = _lex(arg)
        sub = toks[0] if toks else ""
        s = self._need_session()
        if sub == "open" and len(toks) > 1:
            print(s.open_tunnel(toks[1], client=toks[2] if len(toks) > 2 else "ssh"))
        elif sub == "stats":
            st = s.tunnel_stats()
            state = st["status"] if st["open"] else "no tunnel open"
            print(f"{state}: {st['requests']} requests, {st['in']} in, {st['out']} out")
        elif sub == "close":
            s.close_tunnel()
        elif sub == "own-ip":
            print(s.own_ip_tunnel())
        else:
            print("usage: tunnel open <address> [ssh|old-ssh|putty] | stats | close | own-ip")

    # --- verbs: iOS -------------------------------------------------------------
    def do_device(self, arg: str) -> None:
        """device [model] — iOS: restart as another model (`device iPhone 15`); bare, list them."""
        model = arg.strip().strip("\"'")
        s = self._need_session()
        if not model:
            print("\n".join(s.devices()))
            return
        with ui.status(f"restarting iOS as {model} …"):
            state = s.set_device(model)
        ui.success(f"{state}: {model}")

    def do_rotate(self, arg: str) -> None:
        """rotate <0|90|180|270> — iOS: rotate the device, e.g. `rotate 90`."""
        try:
            degrees = int(arg.strip())
        except ValueError:
            raise BlingError("rotate needs 0, 90, 180 or 270: rotate <degrees>") from None
        self._need_session().set_orientation(degrees)

    def do_button(self, arg: str) -> None:
        """button <home|action|mute|volume_down|volume_up|lock> — iOS: press a device button."""
        name = arg.strip()
        if not name:
            print(f"usage: button <{'|'.join(config.IOS_BUTTONS)}>")
            return
        self._need_session().press_button(name)

    def do_devtools(self, arg: str) -> None:
        """devtools — iOS: open Safari's Web Inspector for the page."""
        self._need_session().show_devtools()

    # --- verbs: control ------------------------------------------------------
    def do_wait(self, arg: str) -> None:
        """wait <seconds> — sleep. Handy as an explicit pause in a recording for playback."""
        try:
            seconds = float(arg.strip())
        except ValueError:
            raise BlingError("wait needs a number of seconds: wait <seconds>") from None
        time.sleep(seconds)

    def do_end(self, arg: str) -> None:
        """end — end the Browserling VM. The shell stays open; `open <url>` starts a fresh one."""
        self._need_session().end()
        ui.info("session ended (shell still open - `open <url>` to start another)")

    def do_record(self, arg: str) -> None:
        """record on <file> | record off — toggle recording typed commands mid-session."""
        toks = _lex(arg)
        if toks and toks[0] == "on" and len(toks) > 1:
            self._open_recording(toks[1])
            ui.success(f"recording -> {toks[1]}")
        elif toks and toks[0] == "off":
            was = self._rec_path
            self._close_recording()
            ui.info(f"recording stopped{f': {was}' if was else ''}")
        else:
            print("usage: record on <file> | record off")

    def do_play(self, arg: str) -> None:
        """play <file> — replay a .bling recording line by line (aborts on the first error)."""
        toks = _lex(arg)
        if not toks:
            print("usage: play <file.bling>")
            return
        path = Path(toks[0])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise BlingError(f"cannot read recording {path}: {e}") from e
        stripped = (ln.strip() for ln in text.splitlines())
        cmds = [ln for ln in stripped if ln and not ln.startswith("#")]
        total = len(cmds)
        for i, line in enumerate(cmds, 1):
            ui.info(f"[{i}/{total}] {line}")
            self.onecmd(line)
            if self._error is not None:
                ui.info(f"playback aborted at line {i}: {line}")
                return
        ui.success(f"replayed {total} command{'' if total == 1 else 's'} from {path.name}")

    def do_quit(self, arg: str) -> bool:
        """quit — close the session and exit the shell."""
        return True

    def do_exit(self, arg: str) -> bool:
        """exit — close the session and exit the shell."""
        return True

    def do_EOF(self, arg: str) -> bool:
        """EOF (Ctrl-D on Unix, Ctrl-Z then Enter on Windows) — close the session and exit."""
        print()
        return True

    # --- tab completion (nice-to-have) --------------------------------------
    def complete_proxy(self, text, line, begidx, endidx) -> list[str]:
        """Tab-complete the proxy kinds and `status`."""
        return [k for k in (*_PROXY_KINDS, "status") if k.startswith(text)]

    def complete_tunnel(self, text, line, begidx, endidx) -> list[str]:
        """Tab-complete `tunnel`'s sub-commands."""
        return [k for k in _TUNNEL_SUBS if k.startswith(text)]

    def complete_button(self, text, line, begidx, endidx) -> list[str]:
        """Tab-complete the iOS device buttons."""
        return [k for k in config.IOS_BUTTONS if k.startswith(text)]

    def _complete_path(self, text: str) -> list[str]:
        p = Path(text)
        directory = p.parent if text else Path(".")
        prefix = p.name
        try:
            hits = [c.name for c in directory.iterdir() if c.name.startswith(prefix)]
        except OSError:
            return []
        return [str(directory / name) for name in hits]

    def complete_play(self, text, line, begidx, endidx) -> list[str]:
        """Tab-complete the recording's file name."""
        return self._complete_path(text)


def run_shell(
    *,
    record: str | None = None,
    play: str | None = None,
    headless: bool = False,
    exit_after_play: bool = False,
) -> int:
    """Run a BlingShell: optionally replay a file first, then drop to the interactive prompt.

    ``exit_after_play=True`` replays ``play`` and exits without a prompt — the ``bling play``
    path, which needs no TTY and runs in any context (pipe, cron, background).
    """
    sh = BlingShell(headless=headless)
    if record:
        sh._open_recording(record)
    try:
        if play:
            sh.onecmd(f"play {shlex.quote(play)}")  # onecmd catches errors; won't crash
            if exit_after_play:
                return 1 if sh._error is not None else 0
        ui.banner(__version__, record)
        sh.cmdloop()
        ui.info("session closed.")
        return 0
    finally:
        sh._shutdown()


def run_lines(lines: list[str], *, headless: bool = True) -> int:
    """Run shell command lines in one session, in order, with no prompt: ``bling do``.

    Stops at the first line that fails. Returns 0 if every line ran, else 1. The session is
    always closed at the end.

    >>> run_lines(["open example.com", "screenshot page.png --remote-only"])   # doctest: +SKIP
    0
    """
    sh = BlingShell(headless=headless)
    try:
        for i, line in enumerate(lines, 1):
            sh.onecmd(line)
            if sh._error is not None:
                ui.info(f"stopped at command {i}: {line}")
                return 1
        return 0
    finally:
        sh._shutdown()


def main(argv: list[str] | None = None) -> int:
    """Standalone entry: ``python -m bling.shell [--record F] [--play F] [--headless]``."""
    import argparse

    ap = argparse.ArgumentParser(prog="bling shell", description="Interactive Browserling driver.")
    ap.add_argument("--record", metavar="FILE", help="record typed commands to FILE (.bling)")
    ap.add_argument("--play", metavar="FILE", help="replay FILE, then drop to the prompt")
    ap.add_argument("--headless", action="store_true", help="run without showing the browser")
    args = ap.parse_args(argv)
    return run_shell(record=args.record, play=args.play, headless=args.headless)


if __name__ == "__main__":
    raise SystemExit(main())
