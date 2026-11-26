# Contributing to bling

Read [CODING_STANDARD.md](CODING_STANDARD.md) first. It is short, and it is the bar: an
API someone can guess, errors that say how to fix them, docstrings that are the usage.

## Set up

```bash
git clone https://github.com/BlingWeb/bling.git
cd bling
pip install -e .
pip install pytest ruff
playwright install chrome
bling login                 # only needed for the live tests
```

## Layout

| Path | What lives there |
|---|---|
| `bling/session.py` | `Session`: everything that drives a session |
| `bling/har.py` | `HAR` and the capture functions |
| `bling/config.py` | Every selector, address, pixel position and name that Browserling could change. Nothing brittle goes inline in the logic |
| `bling/errors.py` | The exception classes |
| `bling/cli.py`, `bling/shell.py`, `bling/ui.py` | The `bling` command, the interactive shell, terminal output |
| `tests/` | Offline tests, and live tests marked `live` |
| `docs/` | The guides and the reference; index in [docs/README.md](docs/README.md) |
| `examples/` | Runnable scripts and `.bling` recordings |

## Tests

```bash
pytest                      # offline: no network, no Browserling session; about a minute
ruff check bling tests
```

The offline suite must pass before every commit. It needs no account.

Tests marked `@pytest.mark.live` drive real Browserling machines. They cost session time,
need a signed-in profile, and cannot run in CI because the sign-in has a CAPTCHA. They are
skipped unless you opt in:

```bash
BLING_LIVE=1 pytest tests/test_live_panel.py -x -q
```

In PowerShell, set the variable first: `$env:BLING_LIVE = "1"`.

Run the live tests for the area you touched. A change to a `Session` method that clicks
Browserling's page is not done until it has passed against a live session; say in the
commit message what you ran and when.

What to test where:

- **Offline:** argument checks, parsing, name-to-slug rules, anything with no page. A fake
  `Session` stands in for the shell tests (`tests/test_shell.py`).
- **Live:** one test per feature that proves the real page still answers the way the code
  expects. Share one session across a module (`scope="module"`) to save machines.

## Adding a `Session` method

1. Look at the real page first. Open a session with `headless=False`, use the feature by
   hand, and read the elements involved (`s.page` is the Playwright page). Write down what
   you saw and the date, in a comment, as the existing code does.
2. Put selectors and names in `config.py`.
3. Refuse bad arguments before clicking anything, with a `BlingError` that lists the valid
   choices.
4. Wrap page work in `with self._step("doing the thing"):` so a Playwright timeout
   surfaces as a `BlingError` naming the step. Callers should only ever need to catch
   `BlingError`.
5. Leave the page as you found it: a method that opens a window to do one thing closes it.
6. Docstring: one imperative line, what it returns, and a runnable `>>>` example.
7. Add a shell verb in `shell.py` if a person would type it, and list it in `_HELP_ORDER`.
8. Add an offline test for the refusals and a live test for the real thing.
9. Document it: [docs/API.md](docs/API.md) (signature, parameters, returns, raises,
   example), the "what works where" table in [docs/ENVIRONMENTS.md](docs/ENVIRONMENTS.md),
   [docs/SHELL.md](docs/SHELL.md) for a verb, [docs/ERRORS.md](docs/ERRORS.md) for a new
   message, and [CHANGELOG.md](CHANGELOG.md).

## When Browserling changes its page

Symptoms: a method that worked now raises `menu item ... not clickable`, `... window did
not open` or `timed out ...` on a clean session.

1. Reproduce with `headless=False` and look at the element the error names.
2. Fix the selector in `config.py` (or the JavaScript helper at the top of `session.py`).
3. Add the new shape as a test case where one exists (the transfer-link pattern has a
   parametrised test for exactly this reason).
4. Note what changed and when in a comment and in the changelog.

## Docs

Docs change in the same commit as the behaviour. The reference must never disagree with
the code, and `tests/test_docs.py` holds it to that: it fails when a public method is
missing from [docs/API.md](docs/API.md) or its documented signature differs from the code,
when a shell verb or command-line flag is undocumented, when a public member has no
docstring, or when a link between the Markdown files is broken. If you change an error
message, search `docs/` for the old one. State only what was run; where something was not tried live, the docs say so.

## Versions and releases

The version is in `bling/__init__.py` and nowhere else. bling follows
[semantic versioning](https://semver.org): while it is `0.x`, a minor version may change
behaviour, and the changelog says how. Every user-visible change gets a line in
[CHANGELOG.md](CHANGELOG.md).

## Security

bling never solves the sign-in CAPTCHA and never will. Do not add code that reads the
user's own clipboard, stores credentials, or prints a tunnel command, invite link or
transfer token to a log by default.

Report a vulnerability privately to support@browserling.com, not in a public issue.
