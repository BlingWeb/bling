# bling coding standard

The bar: **`requests`**. Kenneth Reitz's rule, *"if you have to look at the docs every
time you use the module, build a better module"*, and Armin Ronacher's praise of requests
as *"how beautiful an API can be with the right level of abstraction."* We add one twist:
bling is **built for AI use** as much as human use. Both audiences want the same thing,
an obvious, predictable, self-describing API, so we optimize for it deliberately.

This standard is short on purpose. If a rule needs a paragraph to justify, it's probably wrong.

## API design

- **One hero call for the 90% case.** `requests.get(url)` → `bling.har(url)`. A top-level
  function with sensible defaults that Just Works, delegating to a `Session` underneath.
- **`Session` for power users**, always a context manager (`with bling.Session() as s:`) so
  resources (and the remote VM) are always released; a leaked session = "too many sessions".
- **Minimal required args, keyword-only options.** Required things are positional; everything
  else is a keyword with a sane default. No config needed to start.
- **Right level of abstraction.** Hide the two-layer Browserling mess (DOM vs canvas) behind
  verbs. The caller says `s.run("whoami")`, not "focus the canvas, press Win+R, poll a log".
- **Rich return objects, not raw dicts.** Like `Response`: return a small object with obvious
  accessors (`har.entries`, `har.creator`, `har.save(path)`, `len(har)`, a useful `__repr__`).
- **One obvious way.** No flag-for-everything. Pick the right default instead of exposing a knob.

## Built for AI use

- **Docstrings ARE the usage.** Every public function/method opens with a one-line imperative
  summary and a runnable `>>>` example. An agent calling `help(bling.har)` must learn enough
  to use it correctly without reading anything else.
- **Structured, serializable returns.** Prefer plain data (dict/list/str) or thin wrappers over
  it. An agent should be able to `.save()` or inspect results without guessing.
- **Errors tell you how to fix them.** Messages are actionable, not just descriptive:
  `"Not logged in — run: bling login"`, not `"auth error"`. A typed hierarchy lets callers
  branch (`NotLoggedIn`, `SessionBlocked`, `Timeout`, `EgressError`).
- **Fail fast and loud.** Surface failures immediately (Zen: errors never pass silently). No
  silent retries that hide a broken state.
- **Small, stable surface.** Fewer public names = fewer ways to be wrong. Underscore-prefix
  anything internal; export the rest explicitly via `__all__`.
- **Full type hints** on every public signature. Tools and agents reason about them.
- **No hidden global state.** State lives on the `Session`. Operations are deterministic and,
  where sensible, idempotent.

## Code style

- **PEP 8**, 100-col soft limit, 4-space indent. Run `ruff`/`black` if configured.
- **`from __future__ import annotations`** at the top of every module.
- **Flat over nested. Short functions.** If a function doesn't fit on a screen, split it.
- **Verb-first names that say what they do:** `open`, `run`, `upload`, `download`, `capture`.
  Nouns return things; verbs do things.
- **Module layout:** module docstring → imports (stdlib, third-party, local; grouped) →
  constants → classes/functions. Constants and brittle environment specifics (browser paths,
  viewport, coordinates, timeouts) live in `config.py`, never inline in logic.
- **Comments explain WHY, not WHAT.** The code already says what. Comment the non-obvious
  (e.g. *"focus the canvas first so Win+R forwards into the VM"*).
- **Minimal dependencies.** Currently: `playwright`, `requests`, `python-dotenv`. No npm. Add a
  dependency only when it removes real complexity.

## Testing

- **Unit tests need no live session:** token-regex parsing, HAR helpers, URL/host helpers,
  bat-template rendering. These run anywhere.
- **Live integration tests are gated** behind an env flag + a logged-in profile (CAPTCHA means
  CI can't run them). Mark them clearly; never block the unit suite on a session.

## The smell test

Before adding anything, ask: *would an engineer (or an agent) guess this exists, and guess how
to call it, without reading the source?* If not, rename it, give it a default, or delete it.
