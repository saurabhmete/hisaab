"""Checks for the assisted data import. Run with `python -m tests.test_data_import` (or pytest)."""
import copy
import json
import sqlite3
from datetime import date

from app import data_import as DI
from app import database as D

TODAY = date(2026, 4, 10)


def doc():
    return copy.deepcopy(DI.EXAMPLE)


def parse(d, known=()):
    return DI.parse(json.dumps(d), TODAY, known)


def memory_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(D.SCHEMA)
    return db


def test_prompt_lists_my_categories_and_todays_date_and_asks_before_guessing():
    prompt = DI.build_prompt(date(2026, 4, 10), ["Rent", "Groceries"])
    assert "2026-04-10" in prompt and "- Rent" in prompt and "- Groceries" in prompt
    assert "ASK ME" in prompt and "wait for my answers" in prompt and "Never guess" in prompt
    assert "{categories}" not in prompt and "{example}" not in prompt


def test_prompt_copes_with_no_categories_yet():
    assert "none yet" in DI.build_prompt(TODAY, [])


def test_the_example_inside_the_prompt_is_itself_a_valid_import():
    r = parse(DI.EXAMPLE)
    assert r["ok"], r["errors"]
    assert len(r["data"]["months"]) == 2 and len(r["data"]["investments"]) == 1


def test_a_whole_chat_reply_with_a_fenced_block_is_accepted():
    reply = "Here is your file:\n```json\n" + json.dumps(doc()) + "\n```\nAssumptions: none."
    assert DI.parse(reply, TODAY)["ok"]


def test_bad_files_get_clear_messages():
    assert "valid JSON" in DI.parse("nope {", TODAY)["errors"][0]
    bad = doc(); bad["format"] = "hisaab-loan-import"
    assert "marker" in parse(bad)["errors"][0]
    bad = doc(); bad["version"] = 9
    assert "version" in parse(bad)["errors"][0]
    bad = doc(); bad["months"][0]["month"] = 13
    assert any("month" in e for e in parse(bad)["errors"])
    bad = doc(); bad["months"][0]["expenses"][0]["amount"] = -5
    assert any("at least 0" in e for e in parse(bad)["errors"])
    bad = doc(); bad["months"][0]["salary"] = "lots"
    assert any("not a number" in e for e in parse(bad)["errors"])
    bad = doc(); bad["months"].append(dict(bad["months"][0]))
    assert any("more than once" in e for e in parse(bad)["errors"])
    bad = {"format": DI.FORMAT, "version": 1, "months": [], "investments": []}
    assert any("nothing" in e.lower() or "no months" in e.lower() for e in parse(bad)["errors"])


def test_formatted_numbers_and_dict_style_expenses_are_tolerated():
    d = doc()
    d["months"][0]["salary"] = "3,000"
    d["months"][0]["expenses"] = {"Rent": 900, "Gym": "30"}
    r = parse(d)
    assert r["ok"] and r["data"]["months"][0]["expenses"] == {"Rent": 900.0, "Gym": 30.0}


def test_repeated_categories_in_a_month_are_added_together():
    d = doc()
    d["months"][0]["expenses"] = [{"category": "Groceries", "amount": 100.25},
                                  {"category": "groceries", "amount": 50}, {"category": " Groceries ", "amount": 10}]
    r = parse(d)
    assert r["data"]["months"][0]["expenses"] == {"Groceries": 160.25}
    assert any("added together" in i for i in r["info"])


def test_category_names_are_matched_to_the_ones_already_used():
    d = doc()
    d["months"][0]["expenses"] = [{"category": "rent", "amount": 900}, {"category": "GROCERIES", "amount": 10}]
    r = parse(d, known=["Rent", "Groceries"])
    assert set(r["data"]["months"][0]["expenses"]) == {"Rent", "Groceries"}
    assert any("Matched" in i for i in r["info"])


def test_months_with_nothing_in_them_are_skipped_with_a_warning():
    d = doc()
    d["months"].append({"year": 2026, "month": 4, "salary": None, "invested": None, "expenses": []})
    r = parse(d)
    assert r["ok"] and len(r["data"]["months"]) == 2 and any("skipped" in w for w in r["warnings"])


def test_salary_of_zero_with_expenses_looks_like_a_mistake():
    d = doc(); d["months"][0]["salary"] = 0
    assert any("salary is 0" in w for w in parse(d)["warnings"])


def test_months_far_ahead_are_flagged():
    d = doc(); d["months"][0]["year"] = 2028
    assert any("ahead of today" in w for w in parse(d)["warnings"])


def test_investment_profit_may_be_negative_but_values_may_not():
    d = doc(); d["investments"][0]["india_profit"] = -1500
    assert parse(d)["ok"]
    d["investments"][0]["india_value"] = -1
    assert not parse(d)["ok"]


def test_describe_shows_what_is_new_and_what_would_be_replaced():
    db = memory_db()
    mid = D.upsert_month(db, 2026, 2, salary=2800, invested=500)
    D.set_expense(db, mid, "Rent", 850)
    D.set_expense(db, mid, "Gym", 30)
    change = DI.describe(db, parse(DI.EXAMPLE)["data"])
    feb, mar = change["months"]
    assert feb["status"] == "update" and mar["status"] == "new"
    assert feb["was_salary"] == 2800
    rent = next(l for l in feb["lines"] if l["category"] == "Rent")
    groceries = next(l for l in feb["lines"] if l["category"] == "Groceries")
    assert rent["was"] == 850 and groceries["was"] is None
    assert change["new_months"] == 1 and change["updated_months"] == 1 and change["expense_lines"] == 5


def test_describe_warns_when_a_new_investment_row_has_no_rate():
    db = memory_db()
    d = doc(); d["investments"][0]["eur_inr_rate"] = None
    change = DI.describe(db, parse(d)["data"])
    assert any("no EUR→INR rate" in n for n in change["notes"])


def test_create_adds_new_months_and_updates_existing_ones_without_emptying_them():
    db = memory_db()
    mid = D.upsert_month(db, 2026, 2, salary=2800, invested=500)
    D.set_expense(db, mid, "Rent", 850)
    D.set_expense(db, mid, "Gym", 30)                      # not in the file: must survive
    counts = DI.create(db, parse(DI.EXAMPLE)["data"])
    assert counts == {"Months": 2, "Expense entries": 5, "Investment snapshots": 1}

    feb = D.get_month(db, 2026, 2)
    assert feb["salary"] == 3000 and feb["invested"] == 800
    cats = {e["category"]: e["amount"] for e in D.month_expenses(db, feb["id"])}
    assert cats == {"Rent": 900, "Gym": 30, "Groceries": 310.5, "Transport": 49}
    mar = D.get_month(db, 2026, 3)
    assert {e["category"] for e in D.month_expenses(db, mar["id"])} == {"Rent", "Groceries"}


def test_a_missing_salary_leaves_the_stored_one_alone():
    db = memory_db()
    D.upsert_month(db, 2026, 2, salary=2800, invested=500)
    d = {"format": DI.FORMAT, "version": 1,
         "months": [{"year": 2026, "month": 2, "salary": None, "invested": None,
                     "expenses": [{"category": "Rent", "amount": 900}]}]}
    DI.create(db, parse(d)["data"])
    row = D.get_month(db, 2026, 2)
    assert row["salary"] == 2800 and row["invested"] == 500


def test_investment_update_only_touches_the_fields_in_the_file():
    db = memory_db()
    D.upsert_investment(db, 2026, 3, 400000, 50000, 9000, 300, 105.0)
    d = {"format": DI.FORMAT, "version": 1,
         "investments": [{"year": 2026, "month": 3, "india_value": 410000, "india_profit": None,
                          "germany_value": None, "germany_profit": None, "eur_inr_rate": None}]}
    DI.create(db, parse(d)["data"])
    row = db.execute("SELECT * FROM investments WHERE year=2026 AND month=3").fetchone()
    assert (row["india_value"], row["india_profit"], row["germany_value"], row["germany_profit"],
            row["eur_inr_rate"]) == (410000, 50000, 9000, 300, 105.0)


def test_a_new_investment_row_without_a_rate_uses_the_latest_stored_rate():
    db = memory_db()
    D.upsert_investment(db, 2026, 1, 1, 1, 1, 1, 107.5)
    d = {"format": DI.FORMAT, "version": 1,
         "investments": [{"year": 2026, "month": 3, "india_value": 410000, "india_profit": None,
                          "germany_value": None, "germany_profit": None, "eur_inr_rate": None}]}
    DI.create(db, parse(d)["data"])
    assert db.execute("SELECT eur_inr_rate FROM investments WHERE month=3").fetchone()[0] == 107.5


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
