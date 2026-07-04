"""Run a tool in the disposable VM: fingerprint a suspect file without it touching your machine.

The whole point of a throwaway Browserling VM is isolation — you can push a suspect file into
it, run tools against it there, pull back only a text report, and then the VM is destroyed. The
sample never executes (or even lands) on your own disk. This walks the round trip: upload a
file, hash it with a built-in Windows tool, run an uploaded scan script, and download the result.

Run after `bling login`:
    python examples/run_in_vm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # find ./bling without install

import bling

# Files land in the VM user's Downloads (same place bling's own capture writes to).
VM_DOWNLOADS = r"C:\Users\user\Downloads"

# A stand-in "suspect" — swap in a real sample path with s.upload("path\\to\\sample.bin", ...).
SAMPLE = b"harmless demo payload -- pretend this is a suspicious attachment\n"

# A tiny scan tool we upload and run *inside* the VM. Batch keeps the example dependency-free;
# it could just as well be a Python or PowerShell script you `upload` and `run`.
SCAN_BAT = (
    "@echo off\r\n"
    f"echo == size ==\r\n"
    f"for %%F in (\"{VM_DOWNLOADS}\\suspect.bin\") do echo %%~zF bytes\r\n"
    f"echo == sha256 ==\r\n"
    f"certutil -hashfile \"{VM_DOWNLOADS}\\suspect.bin\" SHA256\r\n"
)

with bling.Session(headless=True) as s:
    s.require_login()
    s.open("example.com", browser="firefox")  # any page; we only need the VM's shell + files

    # 1) What does this disposable box even look like from the inside?
    print("VM identity:", s.run("whoami").strip(), "/", s.run("ver").strip())

    # 2) Push the suspect file in. upload_text writes bytes-as-text; use s.upload(path) for a
    #    real local file. It never runs — we only ever inspect it.
    s.upload_text(SAMPLE.decode(), "suspect.bin")
    print("uploaded suspect.bin into the VM's Downloads")

    # 3) Run a built-in tool against it, then an uploaded scan script — all on the VM.
    print("\n--- certutil (built-in) ---")
    print(s.run(rf"certutil -hashfile {VM_DOWNLOADS}\suspect.bin SHA256"))

    s.upload_text(SCAN_BAT, "scan.bat")
    print("--- scan.bat (uploaded tool) ---")
    print(s.run(rf"cmd /c {VM_DOWNLOADS}\scan.bat"))

    # 4) Pull only the artifact you want back out; the sample stays in the VM and dies with it.
    out = s.download("suspect.bin", "suspect-roundtrip.bin")
    print(f"round-tripped the file back out to {out} ({out.stat().st_size} bytes)")

print("\nDone — the VM is now released. Nothing suspicious ran on your machine.")
