# Quickstart

Ten minutes from nothing to driving a remote browser. You need Python 3.10 or later,
Google Chrome on this machine, and a Browserling account.

**Next:** [Advanced usage](ADVANCED.md) · [API reference](API.md) ·
[Command line](CLI.md) · [Troubleshooting](TROUBLESHOOTING.md)

## Install

```bash
git clone https://github.com/BlingWeb/bling.git
cd bling
pip install -e .
playwright install chrome
```

bling drives real Google Chrome, which must be installed; `playwright install chrome` sets
it up as Playwright's Chrome channel. If it says Chrome is already installed, you are done.
Do not re-run it with `--force` as it suggests: that begins by uninstalling your Chrome.

## Sign in once

```bash
bling login
```

A Chrome window opens on Browserling's sign-in. Type your email and password, solve the
CAPTCHA, press Continue. bling never solves the CAPTCHA for you. The sign-in is saved in a
profile folder (`~/.browserling_pw_profile`), and everything after this runs unattended
until the cookie expires. When it does, you get `Not logged in — run once: bling login`.

## Record what a page does

```bash
bling har demo.browserling.com
```

That opens a throwaway Windows machine at Browserling, loads the page in a real Firefox
there, and saves `demo.browserling.com.har`: every request the page made. The page ran on
their machine, never on yours.

```bash
bling urls demo.browserling.com.har     # every URL it requested, one per line
```

The same thing from Python:

```python
import bling

h = bling.har("demo.browserling.com", out="demo.har")
print(len(h), "requests, recorded by", h.creator)
for url in h.urls():
    print(url)
```

## Drive a session yourself

A `Session` is one remote machine. Use it in a `with` block so the machine is released when
you are done.

```python
import bling

with bling.Session() as s:
    s.require_login()                    # fail early if the cookie has expired
    s.open("example.com")                # Windows 10, Chrome
    s.dismiss()                          # close any Browserling announcement
    s.screenshot("page.png", remote_only=True)

    print(s.run("whoami"))               # a shell on the machine, as administrator

    s.upload("tool.py")                  # into the machine's Downloads folder
    s.run("python C:/Users/user/Downloads/tool.py")
    s.download("result.json", "result.json")
```

Add `headless=False` to watch it happen: `bling.Session(headless=False)`.

## See a page as someone else sees it

From another country, on a home connection:

```python
with bling.Session() as s:
    s.require_login()
    s.open("example.com")
    s.set_proxy("residential", country="Germany")
    s.wait_ready()                       # the stream restarts after a proxy change
    s.dismiss()                          # the proxy window stays open otherwise
    s.navigate("https://www.google.com/search?q=browser+sandbox")
    s.screenshot("germany.png", remote_only=True)
```

In another browser, on the same machine:

```python
    s.set_browser("Firefox")             # files and proxy are kept
    s.screenshot("germany-firefox.png", remote_only=True)
```

On another operating system, which is a new machine:

```python
    s.set_os("macOS 15")                 # Safari by default; files and proxy are gone
    s.screenshot("mac.png", remote_only=True)
```

## Or type commands at a prompt

```bash
bling shell --record first.bling
```

```text
bling> open example.com
bling> proxy residential germany
bling> browser Firefox
bling> screenshot germany.png --remote-only
bling> quit
```

`first.bling` now holds those lines. Replay them unattended with `bling play first.bling`,
or skip the file and pass commands straight in:

```bash
bling do "open example.com" "proxy residential germany" "dismiss" "screenshot germany.png --remote-only"
```

The shell has a command for everything the Python API does. They are all in
[SHELL.md](SHELL.md).

## Three things to know before you go further

1. **The remote screen is a picture.** bling can click Browserling's own controls by name,
   but the page inside the remote browser is streamed video. You click it by pixel
   (`s.canvas_click(x, y)`, after looking at a screenshot) or, better, drive it by keyboard.
2. **Browserling's windows stay open.** After `set_proxy()`, `set_resolution()` or a file
   transfer, call `s.dismiss()` or the window will sit in your screenshots and block clicks.
3. **One run per profile folder.** Two programs sharing `~/.browserling_pw_profile` kill
   each other. For parallel runs, give each `Session(profile=...)` its own copy of the
   folder. See [Advanced usage](ADVANCED.md#parallel-sessions).

## Where next

| You want to | Read |
|---|---|
| Know every method, parameter and error | [API.md](API.md) |
| Choose a proxy, run in parallel, use a Mac or an iPhone | [ADVANCED.md](ADVANCED.md) |
| Know which operating systems and browsers exist | [ENVIRONMENTS.md](ENVIRONMENTS.md) |
| Use the command line or the prompt | [CLI.md](CLI.md), [SHELL.md](SHELL.md) |
| Fix an error | [ERRORS.md](ERRORS.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Copy a working script | [examples/](../examples/README.md) |
