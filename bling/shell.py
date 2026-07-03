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
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import __version__
from .errors import BlingError
from .session import Session

# Meta-verbs that control the shell itself — never written to a recording (they don't
# reconstruct a session, and replaying them would be surprising or recursive).
_NO_RECORD = frozenset({"record", "play", "quit", "exit", "EOF", "help", "?"})

# Proxy kinds Browserling offers, for `help proxy` and tab completion.
_PROXY_KINDS = ("datacenter", "residential", "mobile", "tor")


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

    def _close_recording(self) -> None:
        if self._rec is not None:
            self._rec.close()
            self._rec = None
            self._rec_path = None

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
            print(f"error: {e}")
            return False
        except Exception as e:  # noqa: BLE001 — interactive: show it, stay at the prompt
            self._error = e
            print(f"error: unexpected {type(e).__name__}: {e}")
            return False

    def emptyline(self) -> bool:
        """Do nothing on a blank line (cmd.Cmd's default re-runs the last command)."""
        return False

    def default(self, line: str) -> None:
        """Unknown verb — report it and mark an error so a playback aborts here too."""
        self._error = BlingError(f"unknown command: {line.strip()}")
        print(f"error: unknown command: {line.strip()}")

    def cmdloop(self, intro=None) -> None:
        """Interactive prompt. Ctrl-C returns to the prompt; Ctrl-D or `quit` exits."""
        while True:
            try:
                super().cmdloop(intro=intro)
                break
            except KeyboardInterrupt:
                print("\n^C  (Ctrl-D or `quit` to exit)")
                intro = ""  # don't reprint the banner after the first loop

    # --- verbs: session ------------------------------------------------------
    def do_open(self, arg: str) -> None:
        """open <url> [--os win10] [--browser chrome138] — open a session and wait until ready.

        Creates the browser (and checks login) on first use. e.g. `open example.com`.
        """
        pos, opts = _split_opts(_lex(arg), {"--os", "--browser"})
        if not pos:
            print("usage: open <url> [--os win10] [--browser chrome138]")
            return
        kwargs = {}
        if "--os" in opts:
            kwargs["os"] = opts["--os"]
        if "--browser" in opts:
            kwargs["browser"] = opts["--browser"]
        s = self._ensure_session()
        state = s.open(pos[0], **kwargs)
        os_ = kwargs.get("os", "win10")
        browser = kwargs.get("browser", "chrome138")
        print(f"{state}: {pos[0]} on {os_}/{browser}")

    def do_navigate(self, arg: str) -> None:
        """navigate <url> [--via panel|remote] — load a new URL in the running session.

        via=panel (default) uses the control-panel field; via=remote drives the remote
        browser's own address bar (keeps its DevTools open).
        """
        pos, opts = _split_opts(_lex(arg), {"--via"})
        if not pos:
            print("usage: navigate <url> [--via panel|remote]")
            return
        self._need_session().navigate(pos[0], via=opts.get("--via", "panel"))

    def do_proxy(self, arg: str) -> None:
        """proxy <kind> [country] — route the session through a proxy/VPN, then wait ready.

        kind is one of datacenter | residential | mobile | tor. e.g. `proxy mobile "United States"`.
        """
        toks = _lex(arg)
        if not toks:
            print(f"usage: proxy <{'|'.join(_PROXY_KINDS)}> [country]")
            return
        kind = toks[0]
        country = toks[1] if len(toks) > 1 else None
        s = self._need_session()
        print(f"setting {kind} proxy" + (f" (country: {country})" if country else "") + " ...")
        s.set_proxy(kind, country=country)
        print(s.wait_ready(timeout=45))  # re-routing restarts the VM stream

    def do_resolution(self, arg: str) -> None:
        """resolution <WxH> — set the remote screen resolution, e.g. `resolution 1920x1080`."""
        value = arg.strip()
        if not value:
            print("usage: resolution <WxH>")
            return
        self._need_session().set_resolution(value)

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
        value = os.getenv(var)
        if value is None:
            raise BlingError(f"env var {var} is not set — export it before `type_env {var}`")
        self._need_session().type(value)
        print(f"(secret typed from env: {var})")
        self._record_comment(f"(secret typed from env: {var})")

    def do_click(self, arg: str) -> None:
        """click <x> <y> — click a pixel in the remote view, e.g. `click 450 103`."""
        toks = _lex(arg)
        if len(toks) != 2:
            print("usage: click <x> <y>")
            return
        try:
            x, y = int(toks[0]), int(toks[1])
        except ValueError:
            raise BlingError("click needs two integer pixel coordinates: click <x> <y>") from None
        self._need_session().canvas_click(x, y)

    # --- verbs: VM shell + files --------------------------------------------
    def do_run(self, arg: str) -> None:
        """run <command> — run a shell command in the VM and print its output.

        Keep it short (Win+R caps ~255 chars, no embedded double-quotes); for more, upload
        a script and run it by path. e.g. `run whoami`.
        """
        cmd_str = arg.strip()
        if not cmd_str:
            print("usage: run <command>")
            return
        print(self._need_session().run(cmd_str))

    def do_upload(self, arg: str) -> None:
        """upload <local_path> [remote_name] — push a local file into the VM's Downloads."""
        toks = _lex(arg)
        if not toks:
            print("usage: upload <local_path> [remote_name]")
            return
        remote = toks[1] if len(toks) > 1 else None
        name = self._need_session().upload(toks[0], remote)
        print(f"uploaded -> {name}")

    def do_download(self, arg: str) -> None:
        """download <remote_name> <out_path> — pull a file out of the VM to the local machine."""
        toks = _lex(arg)
        if len(toks) != 2:
            print("usage: download <remote_name> <out_path>")
            return
        out = self._need_session().download(toks[0], toks[1])
        print(str(out))

    def do_screenshot(self, arg: str) -> None:
        """screenshot [path] — save a PNG of the session (default: shot.png)."""
        toks = _lex(arg)
        path = toks[0] if toks else "shot.png"
        out = self._need_session().screenshot(path)
        print(str(out))

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
        print("session ended (shell still open — `open <url>` to start another)")

    def do_record(self, arg: str) -> None:
        """record on <file> | record off — toggle recording typed commands mid-session."""
        toks = _lex(arg)
        if toks and toks[0] == "on" and len(toks) > 1:
            self._open_recording(toks[1])
            print(f"(recording -> {toks[1]})")
        elif toks and toks[0] == "off":
            was = self._rec_path
            self._close_recording()
            print(f"(recording stopped{f': {was}' if was else ''})")
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
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue  # skip blanks and comments
            print(f"play> {line}")
            self.onecmd(line)
            if self._error is not None:
                print(f"playback aborted at: {line}")
                return

    def do_quit(self, arg: str) -> bool:
        """quit — close the session and exit the shell."""
        return True

    def do_exit(self, arg: str) -> bool:
        """exit — close the session and exit the shell."""
        return True

    def do_EOF(self, arg: str) -> bool:
        """Ctrl-D — close the session and exit the shell."""
        print()
        return True

    # --- tab completion (nice-to-have) --------------------------------------
    def complete_proxy(self, text, line, begidx, endidx) -> list[str]:
        return [k for k in _PROXY_KINDS if k.startswith(text)]

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
        return self._complete_path(text)


def run_shell(
    *, record: str | None = None, play: str | None = None, headless: bool = False,
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
        intro = (
            f"bling {__version__} — interactive Browserling driver\n"
            "type `help` to list commands, `help <cmd>` for usage, `quit` to exit"
        )
        if record:
            intro += f"\n(recording -> {record})"
        sh.cmdloop(intro)
        print("session closed.")
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
