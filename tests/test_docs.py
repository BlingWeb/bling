"""The docs must not drift from the code. No live session needed.

Checks that every public name is in the API reference with the signature the code has,
that every shell verb and command-line flag is documented, that every public member has a
docstring, and that every link between the Markdown files lands somewhere.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling
from bling.shell import BlingShell

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
API = (DOCS / "API.md").read_text(encoding="utf-8")
MARKDOWN = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md"]
MARKDOWN += sorted(DOCS.glob("*.md")) + [ROOT / "examples" / "README.md"]

SESSION_MEMBERS = sorted(n for n, _ in inspect.getmembers(bling.Session) if not n.startswith("_"))
FUNCTIONS = ["har", "capture", "capture_here", "arm_capture", "sweep_captures", "capture_count"]
FUNCTIONS.append("login")


def _api_section(name: str) -> str:
    """The text under the API.md heading for ``name``, up to the next heading."""
    m = re.search(rf"^#{{2,4}} {re.escape(name)}\n(.*?)(?=^#{{2,4}} )", API, re.S | re.M)
    assert m, f"docs/API.md has no heading for `{name}`"
    return m.group(1)


def _documented_signature(name: str) -> str:
    block = re.search(r"```python\n(.*?)```", _api_section(name), re.S)
    assert block, f"docs/API.md: `{name}` has no signature block"
    return " ".join(block.group(1).split())


# --- API reference -------------------------------------------------------------
@pytest.mark.parametrize("name", SESSION_MEMBERS)
def test_every_session_member_is_in_the_api_reference(name):
    _api_section(name)


@pytest.mark.parametrize(
    "name", [n for n in SESSION_MEMBERS if callable(getattr(bling.Session, n))]
)
def test_session_signatures_match_the_reference(name):
    _check_signature(name, getattr(bling.Session, name), skip_self=True)


@pytest.mark.parametrize("name", FUNCTIONS)
def test_function_signatures_match_the_reference(name):
    _check_signature(name, getattr(bling, name), skip_self=False)


def _check_signature(name, func, *, skip_self: bool) -> None:
    documented = _documented_signature(name)
    params = list(inspect.signature(func).parameters.values())
    if skip_self:
        params = params[1:]
    for p in params:
        assert re.search(rf"\b{p.name}\b", documented), f"`{name}`: `{p.name}` is not documented"
        if p.default is not inspect.Parameter.empty:
            want = f"{p.name}={p.default!r}".replace("'", '"')
            assert want in documented, f"`{name}`: the reference should say `{want}`"
    documented_names = re.findall(r"[(,]\s*\*{0,2}(\w+)\s*(?==|,|\))", documented)
    extra = set(documented_names) - {p.name for p in params}
    assert not extra, f"`{name}`: the reference documents parameters the code lacks: {extra}"


def test_every_exported_name_is_in_the_reference():
    for name in bling.__all__:
        assert name in API, f"docs/API.md never mentions `{name}`"


def test_every_exception_is_in_the_error_reference():
    errors = (DOCS / "ERRORS.md").read_text(encoding="utf-8")
    for name in (
        "BlingError",
        "NotLoggedIn",
        "SessionBlocked",
        "NotReady",
        "Timeout",
        "EgressError",
    ):
        assert name in errors


def test_every_blocking_screen_reason_is_in_the_error_reference():
    errors = (DOCS / "ERRORS.md").read_text(encoding="utf-8")
    for reason in bling.config.FATAL.values():
        assert reason in errors, f"docs/ERRORS.md does not explain {reason!r}"


# --- docstrings ----------------------------------------------------------------
def _public_callables():
    for name in SESSION_MEMBERS:
        yield f"Session.{name}", inspect.getattr_static(bling.Session, name)
    for name in FUNCTIONS:
        yield name, getattr(bling, name)
    for name in dir(bling.HAR):
        if not name.startswith("_"):
            yield f"HAR.{name}", inspect.getattr_static(bling.HAR, name)
    for name, member in vars(BlingShell).items():  # its own verbs, not cmd.Cmd's
        if name.startswith(("do_", "complete_")):
            yield f"BlingShell.{name}", member


@pytest.mark.parametrize("name, member", list(_public_callables()))
def test_every_public_member_has_a_docstring(name, member):
    target = member.fget if isinstance(member, property) else member
    target = getattr(target, "__func__", target)  # unwrap classmethod
    doc = target.__doc__
    assert doc and doc.strip(), f"{name} has no docstring"


# --- shell and command line ----------------------------------------------------
def test_every_shell_verb_is_documented_and_in_help():
    from bling.shell import _HELP_ORDER

    shell = (DOCS / "SHELL.md").read_text(encoding="utf-8")
    verbs = [n[3:] for n in dir(BlingShell) if n.startswith("do_") and n != "do_EOF"]
    for verb in verbs:
        assert re.search(rf"`{verb}\b", shell), f"docs/SHELL.md does not document `{verb}`"
        if verb not in ("help", "exit"):
            assert verb in _HELP_ORDER, f"`{verb}` is missing from the shell's help table"


def test_every_command_line_flag_is_documented():
    cli_doc = (DOCS / "CLI.md").read_text(encoding="utf-8")
    source = (ROOT / "bling" / "cli.py").read_text(encoding="utf-8")
    for flag in sorted(set(re.findall(r'"(--[a-z-]+)"', source))):
        assert flag in cli_doc, f"docs/CLI.md does not document {flag}"
    for command in re.findall(r'add_parser\(\s*"(\w+)"', source):
        assert f"bling {command}" in cli_doc, f"docs/CLI.md does not document `bling {command}`"


def test_the_changelog_covers_the_current_version():
    assert f"## {bling.__version__} " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


# --- links ---------------------------------------------------------------------
def _anchors(text: str) -> set[str]:
    """GitHub's anchor for each heading: lower-case, punctuation dropped, spaces to hyphens."""
    out = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        out.add(slug)
    return out


def _links():
    for path in MARKDOWN:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if not target.startswith(("http://", "https://", "mailto:")):
                yield path, target


@pytest.mark.parametrize("path, target", list(_links()), ids=lambda v: getattr(v, "name", v))
def test_markdown_links_resolve(path, target):
    file_part, _, anchor = target.partition("#")
    dest = (path.parent / file_part).resolve() if file_part else path
    assert dest.exists(), f"{path.name}: link to missing file {target}"
    if anchor and dest.suffix == ".md":
        assert anchor in _anchors(dest.read_text(encoding="utf-8")), (
            f"{path.name}: {target} has no such heading in {dest.name}"
        )
