"""Loan maths checks. Run with `python -m tests.test_loans` (or pytest)."""
from datetime import date

from app import loans as L

START = date(2024, 1, 15)
TODAY = date(2026, 1, 20)   # 24 installments (Feb 2024 .. Jan 2026) are due


def schedule(emis, rates, prepays=(), principal=1_000_000):
    return L.build_schedule(principal, START, emis, rates, prepays, TODAY)


def test_emi_formula_matches_known_value():
    assert abs(L.calc_emi(1_000_000, 8.5, 240) - 8678.23) < 0.05
    assert L.calc_emi(120_000, 0, 12) == 10_000


def test_add_months_clamps_day():
    assert L.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert L.add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert L.add_months(date(2024, 11, 15), 3) == date(2025, 2, 15)


def test_plain_schedule_clears_in_tenure_with_zero_balance():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    rows = schedule([(START, emi)], [(START, 8.5)])
    assert len(rows) == 240
    assert rows[-1]["closing"] == 0
    assert abs(sum(r["principal"] for r in rows) - 1_000_000) < 0.01
    assert abs(rows[0]["interest"] - 1_000_000 * 8.5 / 1200) < 0.01


def test_prepayment_shortens_loan_and_saves_interest():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    base = schedule([(START, emi)], [(START, 8.5)])
    pre = schedule([(START, emi)], [(START, 8.5)], [(date(2025, 1, 15), 200_000)])
    assert len(pre) < len(base)
    assert sum(r["interest"] for r in pre) < sum(r["interest"] for r in base)
    assert abs(sum(r["principal"] + r["prepaid"] for r in pre) - 1_000_000) < 0.01


def test_mid_month_prepayment_saves_less_interest_than_early_one():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    early = schedule([(START, emi)], [(START, 8.5)], [(date(2024, 3, 16), 100_000)])
    late = schedule([(START, emi)], [(START, 8.5)], [(date(2024, 4, 14), 100_000)])
    assert sum(r["interest"] for r in early) < sum(r["interest"] for r in late)


def test_rate_rise_with_same_emi_extends_loan():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    base = schedule([(START, emi)], [(START, 8.5)])
    hiked = schedule([(START, emi)], [(START, 8.5), (date(2025, 1, 15), 10.0)])
    assert len(hiked) > len(base)


def test_emi_change_takes_effect_from_its_start_date():
    rows = schedule([(START, 10_000), (date(2025, 1, 15), 15_000)], [(START, 8.5)])
    by_due = {r["due"]: r for r in rows}
    assert by_due[date(2024, 12, 15)]["emi"] == 10_000
    assert by_due[date(2025, 1, 15)]["emi"] == 15_000


def test_emi_below_interest_is_flagged_never_clears():
    loan = {"principal": 1_000_000, "start_date": START.isoformat(), "tenure_months": None}
    emis, rates = [(START.isoformat(), 5_000)], [(START.isoformat(), 8.5)]
    rows = L.build_schedule(1_000_000, START, emis, rates, [], TODAY)
    s = L.summarize(loan, rows, emis, rates, [], TODAY)
    assert s["never_clears"] and rows[0]["grows"]


def test_summary_outstanding_interest_and_progress():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    loan = {"principal": 1_000_000, "start_date": START.isoformat(), "tenure_months": 240}
    emis, rates = [(START.isoformat(), emi)], [(START.isoformat(), 8.5)]
    rows = L.build_schedule(1_000_000, START, emis, rates, [], TODAY)
    s = L.summarize(loan, rows, emis, rates, [], TODAY)
    assert s["emis_paid"] == 24 and s["emis_left"] == 216
    assert abs(s["outstanding"] - rows[23]["closing"]) < 0.01
    assert abs(s["interest_paid"] - sum(r["interest"] for r in rows[:24])) < 0.01
    assert s["next"]["due"] == date(2026, 2, 15)
    assert s["end_date"] == date(2044, 1, 15) and s["planned_end"] == date(2044, 1, 15)
    assert s["months_ahead"] == 0


def test_prepayment_after_last_due_date_counts_toward_outstanding_today():
    emi = L.calc_emi(1_000_000, 8.5, 240)
    loan = {"principal": 1_000_000, "start_date": START.isoformat(), "tenure_months": 240}
    emis, rates = [(START.isoformat(), emi)], [(START.isoformat(), 8.5)]
    pre = [(date(2026, 1, 18).isoformat(), 50_000)]   # after the 15 Jan installment, before TODAY
    rows = L.build_schedule(1_000_000, START, emis, rates, pre, TODAY)
    s = L.summarize(loan, rows, emis, rates, pre, TODAY)
    assert abs(s["outstanding"] - (rows[23]["closing"] - 50_000)) < 0.01


def test_timeline_derives_end_dates_and_current():
    t = L.timeline([("2024-01-15", 10_000, 1), ("2025-06-01", 12_000, 2)], date(2026, 1, 1))
    assert t[0]["end"] == date(2025, 5, 31) and not t[0]["current"]
    assert t[1]["end"] is None and t[1]["current"]


def test_loan_cleared_by_large_prepayment_is_closed():
    loan = {"principal": 100_000, "start_date": START.isoformat(), "tenure_months": 60}
    emis, rates = [(START.isoformat(), 2_000)], [(START.isoformat(), 8.0)]
    pre = [(date(2025, 6, 1).isoformat(), 500_000)]
    rows = L.build_schedule(100_000, START, emis, rates, pre, TODAY)
    s = L.summarize(loan, rows, emis, rates, pre, TODAY)
    assert s["closed"] and s["outstanding"] == 0 and rows[-1]["closing"] == 0


# ---------- windowed EMI / rate changes, currency migration ----------

import os
import sqlite3
import tempfile

from app import database as D


def _memory_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(D.SCHEMA)
    L.create_loan(db, "Test", "", "USD", 100_000, "2025-01-15", 60, "", 2_000, 8.5)
    return db


def _entries(db, table, column):
    return [(r["start_date"], r[column]) for r in
            db.execute(f"SELECT start_date, {column} FROM {table} WHERE loan_id=1 ORDER BY start_date")]


def test_rate_window_restores_previous_rate_afterwards():
    db = _memory_db()
    L.set_period(db, "rate", 1, "2025-06-15", "2025-12-14", 9.5)
    assert _entries(db, "loan_rates", "rate") == [
        ("2025-01-15", 8.5), ("2025-06-15", 9.5), ("2025-12-15", 8.5)]


def test_window_without_end_is_a_permanent_change():
    db = _memory_db()
    L.set_period(db, "emi", 1, "2025-06-15", None, 2_500)
    assert _entries(db, "loan_emis", "amount") == [("2025-01-15", 2_000), ("2025-06-15", 2_500)]


def test_window_keeps_a_later_entry_instead_of_duplicating_it():
    db = _memory_db()
    L.set_period(db, "rate", 1, "2025-09-01", None, 7.0)          # known future change
    L.set_period(db, "rate", 1, "2025-06-15", "2025-08-31", 9.5)  # window ends right before it
    assert _entries(db, "loan_rates", "rate") == [
        ("2025-01-15", 8.5), ("2025-06-15", 9.5), ("2025-09-01", 7.0)]


def test_window_swallows_entries_inside_it_and_resumes_what_was_in_force():
    db = _memory_db()
    L.set_period(db, "rate", 1, "2025-03-01", None, 9.0)
    L.set_period(db, "rate", 1, "2025-02-01", "2025-04-30", 10.0)
    assert _entries(db, "loan_rates", "rate") == [
        ("2025-01-15", 8.5), ("2025-02-01", 10.0), ("2025-05-01", 9.0)]


def test_window_ending_before_it_starts_is_rejected():
    db = _memory_db()
    try:
        L.set_period(db, "rate", 1, "2025-06-15", "2025-06-01", 9.5)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_rate_window_only_changes_interest_inside_the_window():
    db = _memory_db()
    plain = L.load(db, L.get_loan(db, 1), date(2030, 1, 1))["rows"]
    L.set_period(db, "rate", 1, "2025-06-15", "2025-08-14", 12.0)
    windowed = L.load(db, L.get_loan(db, 1), date(2030, 1, 1))["rows"]
    inside = {date(2025, 7, 15), date(2025, 8, 15)}   # installments priced at 12%
    for a, b in zip(plain[:6], windowed[:6]):
        if b["due"] in inside:
            assert b["interest"] > a["interest"]
    assert windowed[0]["interest"] == plain[0]["interest"]      # before the window
    assert len(windowed) >= len(plain)


def test_every_listed_currency_has_a_symbol():
    assert {"EUR", "INR", "USD", "GBP"} <= set(L.CURRENCIES)
    assert all(L.CURRENCIES.values())


def test_old_two_currency_check_is_rebuilt_without_losing_loans():
    old_schema = """
    CREATE TABLE loans (id INTEGER PRIMARY KEY, name TEXT NOT NULL, lender TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT 'EUR' CHECK (currency IN ('EUR', 'INR')),
        principal REAL NOT NULL CHECK (principal > 0), start_date TEXT NOT NULL,
        tenure_months INTEGER, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE loan_emis (id INTEGER PRIMARY KEY, loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
        start_date TEXT NOT NULL, amount REAL NOT NULL, UNIQUE (loan_id, start_date));
    INSERT INTO loans (name, currency, principal, start_date) VALUES ('Old loan', 'INR', 500000, '2024-01-01');
    INSERT INTO loan_emis (loan_id, start_date, amount) VALUES (1, '2024-01-01', 9000);
    """
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(old_schema)
    raw.commit()
    raw.close()
    saved, D.DB_PATH = D.DB_PATH, path
    try:
        D._drop_loan_currency_check()
        D._drop_loan_currency_check()          # idempotent
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys = ON")
        assert db.execute("SELECT name, currency FROM loans").fetchall() == [("Old loan", "INR")]
        assert db.execute("SELECT amount FROM loan_emis").fetchall() == [(9000.0,)]
        db.execute("INSERT INTO loans (name, currency, principal, start_date) VALUES ('Dollar', 'USD', 1, '2025-01-01')")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.close()
    finally:
        D.DB_PATH = saved


# ---------- importing a running loan ----------

import copy
import json

from app import loan_import as I

TODAY_IMPORT = date(2026, 10, 15)


def _part_payment_loan():
    """A small loan shaped like a real one: 3 months paying 100 against 1000/month
    of interest, then the unpaid interest joins the principal and full EMIs begin."""
    rows = [
        {"due": "2025-01-10", "emi": 100, "principal": 0, "interest": 100, "closing_balance": 120000},
        {"due": "2025-02-10", "emi": 100, "principal": 0, "interest": 100, "closing_balance": 120000},
        {"due": "2025-03-10", "emi": 100, "principal": 0, "interest": 100, "closing_balance": 120000},
        # 3 x (1000 - 100) = 2700 of unpaid interest joins the principal here
        {"due": "2025-04-10", "emi": 2000, "principal": 1000, "interest": 1000, "closing_balance": 121700},
        {"due": "2025-05-10", "emi": 2000, "principal": 1010, "interest": 990, "closing_balance": 120690},
    ]
    return {
        "format": "hisaab-loan-import", "version": 1, "as_of": "2025-06-01",
        "loan": {"name": "Test loan", "lender": "", "currency": "INR", "principal": 120000,
                 "start_date": "2025-01-10", "tenure_months": 60, "notes": ""},
        "emi_history": [{"from": "2025-01-10", "amount": 100}, {"from": "2025-04-10", "amount": 2000}],
        "rate_history": [{"from": "2025-01-10", "rate": 10.0}],
        "prepayments": [], "installments": rows,
    }


def _parse(doc, today=TODAY_IMPORT):
    return I.parse(json.dumps(doc), today)


def test_prompt_mentions_today_and_the_file_format():
    prompt = I.build_prompt(date(2026, 10, 15))
    assert "2026-10-15" in prompt and I.FORMAT in prompt and "closing_balance" in prompt
    assert "{" in prompt and "{example}" not in prompt and "{today}" not in prompt


def test_prompt_makes_claude_ask_before_guessing_and_adapt_to_any_lender():
    prompt = I.build_prompt(date(2026, 10, 15))
    assert "ASK ME" in prompt and "wait for my answers" in prompt
    assert "Never guess" in prompt
    assert "lays its documents out differently" in prompt


def test_the_example_inside_the_prompt_is_itself_a_valid_import():
    r = I.parse(json.dumps(I.EXAMPLE), TODAY_IMPORT)
    assert r["ok"], r["errors"]


def test_import_accepts_a_whole_chat_reply_with_a_fenced_block():
    reply = "Here you go!\n```json\n" + json.dumps(_part_payment_loan()) + "\n```\nAssumptions: none."
    assert I.parse(reply, TODAY_IMPORT)["ok"]


def test_import_finds_interest_added_to_principal_without_being_told():
    r = _parse(_part_payment_loan())
    assert r["ok"], r["errors"]
    capitalised = [x for x in r["data"]["installments"] if x["capitalized"]]
    assert len(capitalised) == 1 and capitalised[0]["due"] == "2025-04-10"
    assert abs(capitalised[0]["capitalized"] - 2700) < 0.01
    assert r["info"] and not r["warnings"]


def test_import_treats_an_unexplained_drop_in_balance_as_an_extra_payment():
    doc = _part_payment_loan()
    doc["installments"][4]["closing_balance"] = 100690          # 20000 lower than expected
    r = _parse(doc)
    assert r["ok"] and r["data"]["installments"][4]["prepaid"] == 20000
    assert any("extra payments" in w for w in r["warnings"])


def test_import_ignores_installments_after_the_statement_date():
    doc = _part_payment_loan()
    doc["as_of"] = "2025-04-15"
    r = _parse(doc)
    assert r["ok"] and len(r["data"]["installments"]) == 4
    assert any("ignored" in w for w in r["warnings"])


def test_import_warns_about_missing_months():
    doc = _part_payment_loan()
    del doc["installments"][1]
    assert any("consecutive" in w for w in _parse(doc)["warnings"])


def test_import_rejects_bad_files_with_clear_messages():
    assert "valid JSON" in I.parse("not json at all {", TODAY_IMPORT)["errors"][0]
    assert I.parse("", TODAY_IMPORT)["errors"] == ["The file is empty."]
    doc = _part_payment_loan()

    bad = copy.deepcopy(doc); bad["format"] = "something-else"
    assert "marker" in _parse(bad)["errors"][0]

    bad = copy.deepcopy(doc); bad["installments"][0]["due"] = "10/01/25"
    assert any("YYYY-MM-DD" in e for e in _parse(bad)["errors"])

    bad = copy.deepcopy(doc); bad["loan"]["currency"] = "XYZ"
    assert any("currency" in e for e in _parse(bad)["errors"])

    bad = copy.deepcopy(doc); bad["rate_history"] = []
    assert any("rate_history" in e for e in _parse(bad)["errors"])

    bad = copy.deepcopy(doc); bad["loan"]["principal"] = -5
    assert any("principal" in e for e in _parse(bad)["errors"])

    bad = copy.deepcopy(doc); bad["installments"].append(dict(doc["installments"][0]))
    assert any("twice" in e for e in _parse(bad)["errors"])


def test_import_tolerates_numbers_sent_as_formatted_strings():
    doc = _part_payment_loan()
    doc["loan"]["principal"] = "1,20,000"
    assert _parse(doc)["ok"]


def test_a_statement_loan_keeps_history_and_projects_from_the_last_recorded_row():
    r = _parse(_part_payment_loan())
    data = r["data"]
    rows = L.build_schedule(data["loan"]["principal"], data["loan"]["start_date"], data["emis"],
                            data["rates"], [], TODAY_IMPORT, data["installments"])
    assert [x["recorded"] for x in rows[:5]] == [True] * 5 and not rows[5]["recorded"]
    assert rows[4]["closing"] == 120690                           # kept exactly as recorded
    assert rows[5]["due"] == date(2025, 6, 10) and rows[5]["n"] == 6
    assert abs(rows[5]["interest"] - 120690 * 10 / 1200) < 0.01   # projection starts from the statement balance
    assert rows[5]["emi"] == 2000 and rows[-1]["closing"] == 0


def test_summary_counts_capitalised_interest_in_what_has_to_be_repaid():
    data = _parse(_part_payment_loan())["data"]
    s = I._preview(data, date(2025, 5, 20))["summary"]      # just after the last recorded row
    assert s["capitalized"] == 2700 and s["has_statement"]
    assert s["outstanding"] == 120690
    # 120000 borrowed + 2700 added to it - 120690 still owed = 2010 of principal actually repaid
    assert abs(s["principal_repaid"] - 2010) < 0.01
    assert abs(s["progress"] - 2010 / 122700) < 1e-9
    assert abs(s["interest_paid"] - (100 + 100 + 100 + 1000 + 990)) < 0.01
    assert s["emis_paid"] == 5 and s["next"]["due"] == date(2025, 6, 10)
    assert s["total_payable"] == 120000 + s["total_interest"]


def test_every_emi_adds_up_to_principal_plus_all_interest_even_with_capitalised_interest():
    data = _parse(_part_payment_loan())["data"]
    rows = L.build_schedule(data["loan"]["principal"], data["loan"]["start_date"], data["emis"],
                            data["rates"], [], TODAY_IMPORT, data["installments"])
    loan = dict(data["loan"], id=0)
    s = L.summarize(loan, rows, data["emis"], data["rates"], [], TODAY_IMPORT)
    assert rows[-1]["closing"] == 0
    assert abs(sum(r["emi"] for r in rows) - s["total_payable"]) < 0.01
    assert abs(s["total_interest"] - (sum(r["interest"] for r in rows) + 2700)) < 0.01


def test_prepayments_inside_the_statement_period_are_not_applied_twice():
    doc = _part_payment_loan()
    doc["prepayments"] = [{"date": "2025-03-20", "amount": 500, "note": "already in the balance"}]
    data = _parse(doc)["data"]
    rows = L.build_schedule(data["loan"]["principal"], data["loan"]["start_date"], data["emis"], data["rates"],
                            [(p["date"], p["amount"]) for p in data["prepayments"]], TODAY_IMPORT,
                            data["installments"])
    assert rows[5]["opening"] == 120690 and rows[5]["prepaid"] == 0


def test_creating_an_imported_loan_stores_every_part_and_loads_back():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(D.SCHEMA)
    loan_id = I.create(db, _parse(_part_payment_loan())["data"])
    loaded = L.load(db, L.get_loan(db, loan_id), TODAY_IMPORT)
    assert len(db.execute("SELECT 1 FROM loan_installments").fetchall()) == 5
    assert [e["value"] for e in loaded["emi_timeline"]] == [100, 2000]
    assert loaded["summary"]["has_statement"] and loaded["summary"]["statement_until"] == date(2025, 5, 10)
    assert sum(1 for r in loaded["rows"] if r["recorded"]) == 5


# ---------- paying a loan off early ----------

def _plain_loan(db=None):
    """100000 at 12% over 12 months, started 2025-01-10 (monthly rate exactly 1%)."""
    db = db or sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(D.SCHEMA)
    emi = round(L.calc_emi(100_000, 12.0, 12), 2)
    L.create_loan(db, "Plain", "", "EUR", 100_000, "2025-01-10", 12, "", emi, 12.0)
    return db


def _closing(db, on, charge=0.0, today=date(2025, 1, 11)):
    data = L.load(db, L.get_loan(db, 1), today)
    return data, L.foreclosure(data["loan"], data["rows"], data["rate_timeline"],
                               data["prepayments"], on, charge)


def test_closing_on_an_emi_date_equals_that_emi_plus_the_balance_after_it():
    data, c = _closing(_plain_loan(), date(2025, 6, 10))
    row = next(r for r in data["rows"] if r["due"] == date(2025, 6, 10))
    assert c["on_due_date"]
    assert abs(c["total"] - (row["emi"] + row["closing"])) < 0.01
    assert abs(c["principal"] - row["opening"]) < 0.01
    assert abs(c["interest"] - row["interest"]) < 0.01


def test_closing_mid_month_charges_interest_only_for_the_days_elapsed():
    data, c = _closing(_plain_loan(), date(2025, 6, 25))        # 15 days after the 10 June installment
    june = next(r for r in data["rows"] if r["due"] == date(2025, 6, 10))
    assert abs(c["principal"] - june["closing"]) < 0.01
    assert c["days"] == 15 and c["period_days"] == 30
    assert abs(c["interest"] - june["closing"] * 0.01 * 15 / 30) < 0.01
    assert not c["on_due_date"] and c["emis_before"] == 5


def test_closing_a_day_after_an_emi_costs_almost_just_the_balance():
    data, c = _closing(_plain_loan(), date(2025, 6, 11))
    june = next(r for r in data["rows"] if r["due"] == date(2025, 6, 10))
    assert abs(c["total"] - june["closing"]) < june["closing"] * 0.01 / 30 + 0.01


def test_closing_adds_the_foreclosure_charge_on_the_principal_only():
    _, plain = _closing(_plain_loan(), date(2025, 6, 25))
    _, charged = _closing(_plain_loan(), date(2025, 6, 25), charge=2.0)
    assert abs(charged["charge"] - plain["principal"] * 0.02) < 0.01
    assert abs(charged["total"] - plain["total"] - charged["charge"]) < 0.01


def test_closing_after_the_loan_would_already_be_repaid_is_nothing_to_pay():
    _, c = _closing(_plain_loan(), date(2030, 1, 1))
    assert c["closed"] and c["total"] == 0


def test_closing_before_the_loan_started_is_rejected():
    try:
        _closing(_plain_loan(), date(2024, 12, 1))
    except ValueError as exc:
        assert "before the loan started" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_closing_accounts_for_a_prepayment_made_earlier_in_the_same_month():
    db = _plain_loan()
    L.add_prepayment(db, 1, "2025-06-15", 10_000, "")
    data, c = _closing(db, date(2025, 6, 25))
    june = next(r for r in data["rows"] if r["due"] == date(2025, 6, 10))   # the period starts here
    assert abs(c["principal"] - (june["closing"] - 10_000)) < 0.01
    # 5 days at the old balance (10 -> 15 June), then 10 days at the reduced one (15 -> 25 June), of a 30-day month
    expected = june["closing"] * 0.01 * 5 / 30 + (june["closing"] - 10_000) * 0.01 * 10 / 30
    assert abs(c["interest"] - expected) < 0.01


def test_closing_a_statement_loan_starts_from_the_recorded_balance_and_refuses_earlier_dates():
    data = _parse(_part_payment_loan())["data"]
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(D.SCHEMA)
    loan_id = I.create(db, data)
    loaded = L.load(db, L.get_loan(db, loan_id), date(2025, 5, 20))
    try:
        L.foreclosure(loaded["loan"], loaded["rows"], loaded["rate_timeline"], [], date(2025, 4, 10))
    except ValueError as exc:
        assert "statement" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    c = L.foreclosure(loaded["loan"], loaded["rows"], loaded["rate_timeline"], [], date(2025, 6, 10))
    assert abs(c["principal"] - 120690) < 0.01 and c["on_due_date"]
    assert abs(c["interest"] - 120690 * 10 / 1200) < 0.01


def test_closing_early_saves_the_interest_of_every_later_installment():
    data, c = _closing(_plain_loan(), date(2025, 6, 10))
    later = sum(r["interest"] for r in data["rows"] if r["due"] > date(2025, 6, 10))
    assert abs(c["interest_saved"] - later) < 0.01


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
