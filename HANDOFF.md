# Setting up bling (headless Browserling driver)

## Install

```bash
pip install -e .
playwright install chrome      # bling drives real Chrome, not Chromium
```

Google Chrome must already be installed on your machine.

## Credentials

Copy `.env.example` to `.env` and fill in a Browserling account (your own account, not
a shared one — the login ties to a local Chrome profile, so two people on one account
can collide with a "another host joined" error):

```
BROWSERLING_EMAIL=you@example.com
BROWSERLING_PASSWORD=your-password-here
```

## Log in once

Browserling's login is reCAPTCHA-gated and is never auto-solved. Do it once, with a
screen available to solve the CAPTCHA — the session cookie then persists in a local
Chrome profile, so every run after this is unattended/headless:

```bash
bling login
```

## Try it

```bash
python examples/quickstart.py
```

or

```python
import bling
har = bling.har("demo.browserling.com", out="demo.har")
print(har.urls())
```

Full API: [`docs/API.md`](docs/API.md). Power-user example (shell, file transfer,
screenshot) in [`examples/quickstart.py`](examples/quickstart.py). Full README with
more detail: [`README.md`](README.md).
