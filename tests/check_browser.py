"""Load every page in a real headless browser and fail on any console error.

Needs Google Chrome or Chromium (it skips politely if neither is found).
Run with `python -m tests.check_browser`.

Browser animations are deliberately left ON: that is how people see the app, and
a chart-library bug once appeared only in that mode (line charts threw
"this._fn is not a function" while every animation-free check stayed green).
"""
import os
import shutil
import subprocess
import sys

from tests.test_pages import COMMON, D, _seed, _start_server

CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]


def find_browser():
    for c in CANDIDATES:
        if os.path.exists(c) or shutil.which(c):
            return c
    return None


def console_messages(browser, url):
    result = subprocess.run(
        [browser, "--headless", "--disable-gpu", "--virtual-time-budget=6000",
         "--enable-logging=stderr", "--v=0", "--dump-dom", url],
        capture_output=True, text=True, timeout=120)
    return [line.strip() for line in result.stderr.splitlines()
            if "CONSOLE" in line and "favicon" not in line.lower()]


def main():
    browser = find_browser()
    if not browser:
        print("skipped: no Chrome or Chromium found")
        return 0
    server, port = _start_server()
    _seed()
    ids = [r[0] for r in D.sqlite3.connect(D.DB_PATH).execute("SELECT id FROM loans")]
    paths = COMMON + ["/?year=2026&month=3", "/month/2026/3"]
    for i in ids:
        paths += [f"/loans/{i}", f"/loans/{i}?close_on=2027-03-10"]
    failures = 0
    for path in paths:
        messages = console_messages(browser, f"http://127.0.0.1:{port}{path}")
        print(("FAIL  " if messages else "ok    ") + path)
        for m in messages[:3]:
            print("        " + m)
        failures += bool(messages)
    print(f"{len(paths) - failures}/{len(paths)} pages clean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
