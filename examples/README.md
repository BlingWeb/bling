# Examples

Runnable walkthroughs. Each needs a one-time `bling login` first.

Two formats: **`.py`** scripts drive the [`Session` API](../docs/API.md) directly; **`.bling`**
recordings are plain-text command scripts you replay with `bling play <file>` (or paste line by
line into `bling shell`) — see [docs/SHELL.md](../docs/SHELL.md). The `.bling` files double as the
record-and-replay demo: they're exactly what `bling shell --record` produces, and they're
hand-editable and safe to paste into a bug report.

| Example | What it shows |
|---|---|
| **[quickstart.py](quickstart.py)** | The HAR one-liner, and a power-user `Session` (shell, file round-trip, screenshot). |
| **[url_triage.py](url_triage.py)** | Triage a captured HAR — list the third-party / exfil domains a page contacted, busiest first. No session needed. `python examples/url_triage.py <file.har>` |
| **[geo_cloaking.py](geo_cloaking.py)** | Capture a URL as served to different **countries** and diff the network requests. `python examples/geo_cloaking.py <url>` |
| **[env_cloaking.py](env_cloaking.py)** | Screenshot a URL across **browsers and OSes** to catch a page that hides from modern browsers (User-Agent sniffing). `python examples/env_cloaking.py <url>` |
| **[run_in_vm.py](run_in_vm.py)** | Push a suspect file into the disposable VM, fingerprint it with tools there, pull back only a report — nothing runs on your machine. |
| **[click_through.bling](click_through.bling)** | A recording: capture one HAR **per page** across a multi-step click-through (capture mode). `bling play examples/click_through.bling` |
| **[login_capture.bling](login_capture.bling)** | A recording: sign in *inside* the capture Firefox with secrets from the environment, then record the authenticated pages. `bling play examples/login_capture.bling` |

The two cloaking scripts are siblings: `geo_cloaking.py` diffs the **network** (HAR) across
countries, while `env_cloaking.py` diffs the **rendered page** (screenshots) across
browsers/OSes — separate axes because bling's HAR capture is always Firefox-on-Windows.
