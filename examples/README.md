# Examples

Runnable walkthroughs. Each needs a one-time `bling login` first.

| Example | What it shows |
|---|---|
| **[quickstart.py](quickstart.py)** | The HAR one-liner, and a power-user `Session` (shell, file round-trip, screenshot). |
| **[geo_cloaking.py](geo_cloaking.py)** | Capture a URL as served to different countries and diff them — a HAR + screenshot per country. `python examples/geo_cloaking.py <url>` |

## Walkthroughs we could add

Ideas for further worked examples, roughly in order of usefulness for the security-lab audience:

- **Click-through capture** — a payload that only appears after clicking through a few pages:
  `capture start`, drive through with `navigate`/`click`, `capture save <dir>`, then inspect the
  chain of per-page HARs. (Shows off capture mode.)
- **Browser-based cloaking** — the same URL opened as Chrome vs Firefox vs IE11 (`--browser`),
  to catch a page that sniffs the User-Agent and hides from modern browsers.
- **OS-based cloaking** — the same URL across `win10` / `win11` / `android` / `ios`, for pages
  that serve a mobile-only or Windows-only payload.
- **Login-gated capture** — sign into a site by hand inside the VM (`bling open --keep-open`, or
  `type_env` for secrets in the shell), then capture the post-login page's traffic.
- **Record & replay a repro** — record a driving session to a `.bling` file and replay it
  unattended, e.g. to reproduce a rendering bug the same way every time across browsers.
- **URL triage from the CLI** — capture a suspicious page, then `bling urls out.har` piped through
  a filter to list every third-party / exfil domain it talked to.
- **Run a tool in the disposable VM** — `upload` a script into the VM, `run` it, `download` the
  results, all isolated from your machine.

Pick any and it becomes the next file here.
