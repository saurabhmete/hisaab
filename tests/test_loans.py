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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
