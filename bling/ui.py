"""Presentation layer — all the rich chrome lives here so the library core stays plain.

The rule that keeps bling usable by scripts and agents as well as humans: **chrome goes to
stderr, data goes to stdout.** Spinners, banners, tables, help, and error panels render on
``err`` (stderr); anything a caller might pipe or parse — a VM command's output, a saved
file path — is written plainly on stdout by the caller. rich drops all styling on its own
when the stream isn't a real terminal and honours the ``NO_COLOR`` convention, so the pretty
build and the machine-readable build are the same binary.
"""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Chrome (spinners, banners, help, errors) → stderr. Human-facing result summaries → stdout.
err = Console(stderr=True)
out = Console()


def status(message: str):
    """A spinner context manager on stderr; inert (no output) when not a terminal.

    >>> with status("opening session"):   # doctest: +SKIP
    ...     do_the_slow_thing()
    """
    return err.status(Text(message, style="cyan"), spinner="dots")


def info(message: str) -> None:
    """A dim, secondary line (feedback, not a result)."""
    err.print(message, style="dim")


def success(message: str) -> None:
    """A green confirmation line."""
    err.print(message, style="green")


def error_panel(exc: object) -> None:
    """Render a bling error as a red panel — the message already says how to fix it."""
    err.print(Panel(Text(str(exc)), title="error", title_align="left", border_style="red"))


def banner(version: str, recording: str | None) -> None:
    """The interactive shell's welcome panel."""
    body = Text.assemble(
        ("bling ", "bold cyan"),
        (version, "cyan"),
        (" - interactive Browserling driver\n", "bold"),
        ("type ", "dim"),
        ("help", "yellow"),
        (" for commands, ", "dim"),
        ("help <cmd>", "yellow"),
        (" for usage, ", "dim"),
        ("quit", "yellow"),
        (" to exit", "dim"),
    )
    if recording:
        body.append(f"\nrecording -> {recording}", style="red")
    err.print(Panel(body, border_style="cyan", expand=False))


def command_help(rows: list[tuple[str, str]]) -> None:
    """Render the shell's command list as a two-column table."""
    table = Table(
        title="bling shell commands",
        title_style="bold",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("command", style="yellow", no_wrap=True)
    table.add_column("what it does")
    for verb, desc in rows:
        table.add_row(verb, desc)
    err.print(table)


def styled_prompt(recording: bool) -> str:
    """The shell prompt string, with a red REC marker while recording (plain when not a TTY).

    Uses raw ANSI rather than rich markup because the prompt is written by ``input()``, not by
    a Console. Plain ``bling> `` when stdout isn't a terminal (e.g. under a test harness).
    """
    if not out.is_terminal:
        return "bling> "
    # ASCII marker on purpose: the prompt is written by input() straight to the console, so
    # it must encode on a legacy Windows code page too (a fancier glyph can raise there).
    rec = "\033[1;31m[REC]\033[0m " if recording else ""
    return f"{rec}\033[1;36mbling>\033[0m "


def har_summary(har: object) -> None:
    """Print a small summary table for a captured HAR (a human result, so it goes to stdout)."""
    codes: Counter[int] = Counter()
    for entry in getattr(har, "entries", []):
        try:
            codes[entry["response"]["status"]] += 1
        except (KeyError, TypeError):
            pass
    table = Table(
        title=f"HAR: {getattr(har, 'url', '')}",
        title_style="bold",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("metric", style="cyan")
    table.add_column("value")
    table.add_row("requests", str(len(har)))
    table.add_row("creator", getattr(har, "creator", None) or "?")
    if codes:
        breakdown = ", ".join(f"{code}: {n}" for code, n in sorted(codes.items()))
        table.add_row("status codes", breakdown)
    out.print(table)
