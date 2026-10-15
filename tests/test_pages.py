"""Every page renders, with an empty database and with data in it.

Starts the real app on a spare port (standard library only, no extra packages)
and fetches each page. Run with `python -m tests.test_pages` (or pytest).
"""
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date

os.environ["HISAAB_AUTH"] = "off"

import uvicorn

from app import database as D
from app import loan_import as I
from app import loans as L
from app import rates
from app.main import app as web
from tests.test_loans import _part_payment_loan


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server():
    D.DB_PATH = os.path.join(tempfile.mkdtemp(), "pages.db")
    rates.eur_inr = lambda: None                      # never touch the network in tests
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(web, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server, port
        time.sleep(0.05)
    raise RuntimeError("server did not start")


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _stray_head_text(html):
    """Text that would be shown on the page but sits in <head> (a sign of a broken tag)."""
    head = re.search(r"<head>(.*?)</head>", html, re.S)
    if not head:
        return ""
    return re.sub(r"<script.*?</script>|<title>.*?</title>|<[^>]+>", "", head.group(1), flags=re.S).strip()


def test_the_stray_text_check_catches_a_broken_icon_tag():
    broken = '<head><link rel="icon" href="data:image/svg+xml,<svg></svg>"><rect x="1"/></svg>"></head>'
    assert _stray_head_text(broken)
    assert not _stray_head_text('<head><meta charset="utf-8"><title>x</title><script>1</script></head>')


def _assert_pages(port, paths):
    for path in paths:
        status, body = _get(port, path)
        assert status == 200, f"{path} -> {status}"
        assert "<title>" in body, f"{path} has no title"
        for bad in ("Traceback", "UndefinedError", "Internal Server Error"):
            assert bad not in body, f"{path} contains {bad!r}"
        assert not _stray_head_text(body), f"{path} has visible text in <head>: {_stray_head_text(body)[:60]!r}"


def _seed():
    with D.get_db(D.DB_PATH) as db:
        for m in (1, 2, 3):
            mid = D.upsert_month(db, 2026, m, salary=3000, invested=500)
            for cat, amount in {"Rent": 900, "Groceries": 300 + m, "Gym": 30}.items():
                D.set_expense(db, mid, cat, amount)
        D.upsert_investment(db, 2026, 2, 400000, 50000, 9000, 300, 105.0)
        D.upsert_investment(db, 2026, 3, 415000, 52000, 9200, 330, 106.0)
        L.create_loan(db, "Car", "Bank", "EUR", 20000, "2025-06-10", 60, "", 400.0, 6.5)
        parsed = I.parse(json.dumps(_part_payment_loan()), date.today())
        assert parsed["ok"], parsed["errors"]
        I.create(db, parsed["data"])


COMMON = ["/", "/years", "/investments", "/loans", "/loans/new", "/loans/import", "/import", "/users",
          "/login", "/month/2026/4", "/year/2026"]


def test_every_page_renders_on_an_empty_database():
    server, port = _start_server()
    _assert_pages(port, COMMON + ["/?year=2026&month=3", "/loans/999"])    # unknown loan redirects to the list


def test_every_page_renders_with_data_including_loans_and_payoff():
    server, port = _start_server()
    _seed()
    loan_ids = [r[0] for r in D.sqlite3.connect(D.DB_PATH).execute("SELECT id FROM loans")]
    paths = COMMON + ["/?year=2026&month=3", "/month/2026/3", "/month/2026/5", "/year/2026"]
    for i in loan_ids:
        paths += [f"/loans/{i}", f"/loans/{i}?close_on=2027-03-10&close_charge=1",
                  f"/loans/{i}?close_on=not-a-date", f"/loans/{i}?close_on=1990-01-01"]
    _assert_pages(port, paths)
    status, body = _get(port, f"/loans/{loan_ids[0]}?close_on=2027-03-10")
    assert "you pay" in body


def test_the_shell_has_navigation_and_the_active_page_is_marked():
    server, port = _start_server()
    status, body = _get(port, "/loans")
    assert 'aria-current="page"' in body and 'class="nav-item active"' in body
    for label in ("Overview", "History", "Investments", "Loans", "Import"):
        assert label in body


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
