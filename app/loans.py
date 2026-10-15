"""Loan / EMI maths and storage.

Model: a reducing-balance loan paid in monthly installments. Installment `i`
falls due `i` months after `start_date` (day clamped to month length). EMI and
interest rate each have their own history of (effective-from date, value)
entries; the value in force on a due date is the latest entry starting on or
before it. Prepayments reduce the outstanding principal on their date, and the
interest of that month is weighted by how long each balance was outstanding.
"""
from calendar import monthrange
from datetime import date, timedelta

MAX_MONTHS = 1200  # guard against EMIs that never clear the loan
EPS = 0.005

# code -> symbol shown before amounts
CURRENCIES = {
    "EUR": "€", "INR": "₹", "USD": "$", "GBP": "£",
    "CHF": "CHF ", "AED": "AED ", "CAD": "C$", "AUD": "A$",
}


def parse_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def calc_emi(principal: float, annual_rate: float, months: int) -> float:
    """Standard reducing-balance EMI."""
    if months <= 0:
        raise ValueError("Tenure must be at least one month.")
    r = annual_rate / 1200
    if r == 0:
        return principal / months
    growth = (1 + r) ** months
    return principal * r * growth / (growth - 1)


RATE_TIMINGS = {
    "on_emi_date": "From the EMI date it's effective on (the EMI due that day already uses the new rate)",
    "next_emi": "From the next EMI (the EMI due that day still uses the old rate; the new rate starts with the following one)",
}


def _rate_for(rates, due, prev_due, timing):
    """Annual rate used for the installment due on `due` (its period began at `prev_due`)."""
    return _in_force(rates, prev_due if timing == "next_emi" else due)


def _in_force(periods, on: date):
    """Value of the latest (start, value) entry starting on/before `on`.
    Before the first entry, the first entry applies."""
    value = periods[0][1]
    for start, v in periods:
        if start <= on:
            value = v
        else:
            break
    return value


def build_schedule(principal, start, emis, rates, prepayments, today, recorded=(),
                   rate_applies="on_emi_date"):
    """Amortization rows until the loan clears (or MAX_MONTHS).

    emis / rates: iterables of (start_date, value); prepayments: (date, amount).
    recorded: installments taken from the lender's own statement (dicts with
    due, emi, principal, interest, prepaid, capitalized, closing). They are
    history and are never recalculated; the projection continues from the last
    one, and prepayments dated on/before it are already in its balance.
    """
    emis = sorted((parse_date(s), v) for s, v in emis)
    rates = sorted((parse_date(s), v) for s, v in rates)
    prepays = sorted((parse_date(d), a) for d, a in prepayments)
    start = parse_date(start)
    if not emis or not rates:
        return []

    rows, balance, prev_due, pi = [], float(principal), start, 0
    base, offset = start, 0
    past = sorted(recorded, key=lambda r: parse_date(r["due"]))
    for r in past:
        due = parse_date(r["due"])
        rows.append({
            "n": len(rows) + 1, "due": due, "opening": balance, "emi": r["emi"],
            "interest": r["interest"], "principal": r["principal"],
            "prepaid": r.get("prepaid", 0.0), "capitalized": r.get("capitalized", 0.0),
            "closing": r["closing"], "paid": due <= today, "grows": False, "recorded": True,
        })
        balance = r["closing"]
    if past:
        base = prev_due = rows[-1]["due"]
        offset = len(rows)
        prepays = [(d, a) for d, a in prepays if d > base]
    for i in range(1, MAX_MONTHS + 1):
        if balance <= EPS:
            break
        due = add_months(base, i)
        days = (due - prev_due).days
        monthly_rate = _rate_for(rates, due, prev_due, rate_applies) / 1200
        emi = _in_force(emis, due)

        seg_balance, seg_from = balance, prev_due
        interest = prepaid = 0.0
        while pi < len(prepays) and prepays[pi][0] <= due:
            when, amount = prepays[pi]
            pi += 1
            when = max(when, prev_due)
            amount = min(amount, seg_balance)
            interest += seg_balance * monthly_rate * (when - seg_from).days / days
            seg_balance -= amount
            prepaid += amount
            seg_from = when
        interest += seg_balance * monthly_rate * (due - seg_from).days / days

        payment = min(emi, seg_balance + interest)
        principal_paid = payment - interest
        closing = seg_balance - principal_paid
        if closing < EPS:
            closing = 0.0

        rows.append({
            "n": offset + i, "due": due, "opening": balance, "emi": payment,
            "interest": interest, "principal": principal_paid, "prepaid": prepaid,
            "capitalized": 0.0, "closing": closing, "paid": due <= today,
            "grows": closing > balance - prepaid + EPS, "recorded": False,
        })
        balance, prev_due = closing, due
    return rows


def timeline(periods, today: date):
    """[(start, value)] -> display rows with a derived end date (the day before
    the next entry starts) and a flag for the entry in force today."""
    items = sorted((parse_date(s), v, pid) for s, v, pid in periods)
    out = []
    for idx, (s, v, pid) in enumerate(items):
        end = items[idx + 1][0] - timedelta(days=1) if idx + 1 < len(items) else None
        out.append({"id": pid, "start": s, "end": end, "value": v,
                    "current": s <= today and (end is None or end >= today)})
    # Before every entry starts, the first one is what the schedule uses.
    if out and not any(r["current"] for r in out) and today < out[0]["start"]:
        out[0]["current"] = True
    return out


def summarize(loan, rows, emis, rates, prepayments, today):
    principal = loan["principal"]
    start = parse_date(loan["start_date"])
    prepays = [(parse_date(d), a) for d, a in prepayments]
    paid = [r for r in rows if r["paid"]]
    upcoming = [r for r in rows if not r["paid"]]

    if paid:
        outstanding = paid[-1]["closing"]
        later = sum(a for d, a in prepays if paid[-1]["due"] < d <= today)
    else:
        outstanding = principal
        later = sum(a for d, a in prepays if d <= today)
    outstanding = max(0.0, outstanding - later)

    sorted_emis = sorted((parse_date(s), v) for s, v in emis)
    sorted_rates = sorted((parse_date(s), v) for s, v in rates)
    closes = bool(rows) and rows[-1]["closing"] <= EPS
    end_date = rows[-1]["due"] if closes else None
    planned_end = add_months(start, loan["tenure_months"]) if loan["tenure_months"] else None

    capitalized = sum(r["capitalized"] for r in rows)
    # Interest that went unpaid and was added to the principal is still interest
    # the borrower pays, through the principal part of later installments.
    total_interest = sum(r["interest"] for r in rows) + capitalized
    repaid = principal + sum(r["capitalized"] for r in paid) - outstanding
    return {
        "outstanding": outstanding,
        "principal_repaid": repaid,
        "capitalized": capitalized,
        "has_statement": any(r["recorded"] for r in rows),
        "statement_until": max((r["due"] for r in rows if r["recorded"]), default=None),
        "progress": min(1.0, max(0.0, repaid / (principal + capitalized))),
        "interest_paid": sum(r["interest"] for r in paid),
        "emis_paid": len(paid),
        "emis_left": len(upcoming),
        "next": upcoming[0] if upcoming else None,
        "current_emi": _in_force(sorted_emis, today) if sorted_emis else None,
        "current_rate": _in_force(sorted_rates, today) if sorted_rates else None,
        "total_interest": total_interest,
        "total_payable": principal + total_interest,
        "prepaid_total": sum(a for d, a in prepays if d <= today),
        "end_date": end_date,
        "planned_end": planned_end,
        "months_ahead": months_between(end_date, planned_end) if end_date and planned_end else None,
        "closed": outstanding <= EPS,
        "never_clears": not closes and bool(rows),
    }


def foreclosure(loan, rows, rate_timeline, prepayments, on, charge_pct=0.0):
    """What it takes to pay the loan off completely on date `on`.

    Every installment due before `on` is assumed paid on schedule. The payoff is
    the principal still outstanding that day, plus interest accrued since the
    last installment, plus an optional foreclosure charge on the principal.

    * An installment falling due exactly on `on` is part of the payoff (its full
      period's interest, worked out as in the schedule), not paid on top of it.
    * Between installments interest accrues daily on actual days / 365, which is
      how lenders quote a part-month. Prepayments made in that stretch count
      from the day they were paid.
    * The rate is the one in force that day; for loans whose lender applies a
      rate change only from the next EMI it is the rate the last EMI was billed
      at, since the lender hasn't applied the reset yet.
    """
    timing = loan.get("rate_applies") or "on_emi_date"
    start = parse_date(loan["start_date"])
    if on < start:
        raise ValueError("That date is before the loan started.")
    recorded_until = max((r["due"] for r in rows if r["recorded"]), default=None)
    if recorded_until is not None and on <= recorded_until:
        raise ValueError(
            f"Pick a date after {recorded_until.isoformat()}: installments up to then are "
            "fixed by your lender's statement.")

    before = [r for r in rows if r["due"] < on]
    after = [r for r in rows if r["due"] >= on]
    if not after:
        return {"on": on, "closed": True, "total": 0.0, "principal": 0.0, "interest": 0.0,
                "charge": 0.0, "charge_pct": charge_pct, "emis_before": len(before)}

    prev_due = before[-1]["due"] if before else start
    balance = before[-1]["closing"] if before else float(loan["principal"])
    nxt = after[0]
    on_due_date = on == nxt["due"]
    period_days = (nxt["due"] - prev_due).days
    rates = sorted((parse_date(r["start"]), r["value"]) for r in rate_timeline)

    if on_due_date:
        rate = _rate_for(rates, nxt["due"], prev_due, timing)
    elif timing == "next_emi":
        billed_from = before[-2]["due"] if len(before) >= 2 else start
        rate = _in_force(rates, billed_from)          # what the last EMI was billed at
    else:
        rate = _in_force(rates, on)

    def accrue(amount, days):
        if on_due_date:                               # a whole period: same as the schedule
            return amount * rate / 1200 * days / period_days
        return amount * rate / 100 / 365 * days       # part of a period: daily, actual/365

    seg_balance, seg_from, interest = balance, prev_due, 0.0
    for when, amount in sorted((parse_date(p["date"]), p["amount"]) for p in prepayments):
        if not prev_due < when <= on:
            continue
        interest += accrue(seg_balance, (when - seg_from).days)
        seg_balance -= min(amount, seg_balance)
        seg_from = when
    interest += accrue(seg_balance, (on - seg_from).days)

    charge = seg_balance * charge_pct / 100
    interest_saved = sum(r["interest"] for r in after[1:]) + max(0.0, nxt["interest"] - interest)
    return {
        "on": on, "closed": False, "principal": seg_balance, "interest": interest,
        "charge": charge, "charge_pct": charge_pct, "total": seg_balance + interest + charge,
        "since": prev_due, "days": (on - prev_due).days, "period_days": period_days,
        "on_due_date": on_due_date, "emis_before": len(before),
        "interest_saved": interest_saved, "rate": rate,
        "rate_on_file": timing == "next_emi" and not on_due_date,
    }


def yearly(rows):
    """Principal vs interest per calendar year, for the chart."""
    years = {}
    for r in rows:
        y = years.setdefault(r["due"].year, {"principal": 0.0, "interest": 0.0})
        y["principal"] += r["principal"] + r["prepaid"]
        y["interest"] += r["interest"]
    return [{"year": y, **v} for y, v in sorted(years.items())]


# ---------- storage ----------

def create_loan(db, name, lender, currency, principal, start_date, tenure_months,
                notes, emi, rate, rate_applies="on_emi_date") -> int:
    cur = db.execute(
        """INSERT INTO loans (name, lender, currency, principal, start_date, tenure_months, notes,
                              rate_applies)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name.strip(), lender.strip(), currency, principal, start_date, tenure_months, notes.strip(),
         rate_applies),
    )
    loan_id = cur.lastrowid
    if emi is not None:
        set_emi(db, loan_id, start_date, emi)
    if rate is not None:
        set_rate(db, loan_id, start_date, rate)
    return loan_id


def update_loan(db, loan_id, name, lender, currency, principal, start_date, tenure_months, notes,
                rate_applies="on_emi_date"):
    db.execute(
        """UPDATE loans SET name=?, lender=?, currency=?, principal=?, start_date=?,
                  tenure_months=?, notes=?, rate_applies=? WHERE id=?""",
        (name.strip(), lender.strip(), currency, principal, start_date, tenure_months,
         notes.strip(), rate_applies, loan_id),
    )


def delete_loan(db, loan_id):
    db.execute("DELETE FROM loans WHERE id=?", (loan_id,))


def get_loan(db, loan_id):
    return db.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()


def all_loans(db):
    return db.execute("SELECT * FROM loans ORDER BY created_at, id").fetchall()


def set_emi(db, loan_id, start_date, amount):
    db.execute(
        """INSERT INTO loan_emis (loan_id, start_date, amount) VALUES (?, ?, ?)
           ON CONFLICT(loan_id, start_date) DO UPDATE SET amount=excluded.amount""",
        (loan_id, start_date, amount),
    )


def set_rate(db, loan_id, start_date, rate):
    db.execute(
        """INSERT INTO loan_rates (loan_id, start_date, rate) VALUES (?, ?, ?)
           ON CONFLICT(loan_id, start_date) DO UPDATE SET rate=excluded.rate""",
        (loan_id, start_date, rate),
    )


_KINDS = {"emi": ("loan_emis", "amount"), "rate": ("loan_rates", "rate")}


def _value_on(db, table, column, loan_id, on):
    """Value of the entry in force on ISO date `on` (the first entry if `on`
    precedes them all), or None when the loan has no entries."""
    row = db.execute(
        f"SELECT {column} AS v FROM {table} WHERE loan_id=? AND start_date<=? "
        f"ORDER BY start_date DESC LIMIT 1", (loan_id, on)).fetchone()
    if row is None:
        row = db.execute(
            f"SELECT {column} AS v FROM {table} WHERE loan_id=? ORDER BY start_date LIMIT 1",
            (loan_id,)).fetchone()
    return row["v"] if row else None


def set_period(db, kind, loan_id, start, end, value):
    """Record `value` (an EMI or a rate) from `start`. With `end`, it applies
    only through that day and whatever was in force afterwards resumes — the
    shape of a floating-rate reset or a temporary EMI. Entries recorded inside
    the window are replaced by it."""
    table, column = _KINDS[kind]
    if not end:
        db.execute(
            f"INSERT INTO {table} (loan_id, start_date, {column}) VALUES (?, ?, ?) "
            f"ON CONFLICT(loan_id, start_date) DO UPDATE SET {column}=excluded.{column}",
            (loan_id, start, value))
        return
    first, last = parse_date(start), parse_date(end)
    if last < first:
        raise ValueError("The 'until' date can't be before the start date.")
    resume_on = (last + timedelta(days=1)).isoformat()
    resume_value = _value_on(db, table, column, loan_id, resume_on)
    has_entry_at_resume = db.execute(
        f"SELECT 1 FROM {table} WHERE loan_id=? AND start_date=?", (loan_id, resume_on)).fetchone()
    db.execute(f"DELETE FROM {table} WHERE loan_id=? AND start_date>=? AND start_date<=?",
               (loan_id, start, last.isoformat()))
    db.execute(f"INSERT INTO {table} (loan_id, start_date, {column}) VALUES (?, ?, ?)",
               (loan_id, start, value))
    if resume_value is not None and not has_entry_at_resume:
        db.execute(f"INSERT INTO {table} (loan_id, start_date, {column}) VALUES (?, ?, ?)",
                   (loan_id, resume_on, resume_value))


def delete_emi(db, loan_id, emi_id):
    """Refuses to delete the only remaining entry (a loan always needs an EMI)."""
    n = db.execute("SELECT COUNT(*) AS c FROM loan_emis WHERE loan_id=?", (loan_id,)).fetchone()["c"]
    if n > 1:
        db.execute("DELETE FROM loan_emis WHERE id=? AND loan_id=?", (emi_id, loan_id))


def delete_rate(db, loan_id, rate_id):
    n = db.execute("SELECT COUNT(*) AS c FROM loan_rates WHERE loan_id=?", (loan_id,)).fetchone()["c"]
    if n > 1:
        db.execute("DELETE FROM loan_rates WHERE id=? AND loan_id=?", (rate_id, loan_id))


def add_installments(db, loan_id, installments):
    for r in installments:
        db.execute(
            """INSERT INTO loan_installments
                   (loan_id, due_date, emi, principal, interest, prepaid, capitalized, closing)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (loan_id, r["due"], r["emi"], r["principal"], r["interest"],
             r.get("prepaid", 0.0), r.get("capitalized", 0.0), r["closing"]))


def add_prepayment(db, loan_id, when, amount, note):
    db.execute(
        "INSERT INTO loan_prepayments (loan_id, date, amount, note) VALUES (?, ?, ?, ?)",
        (loan_id, when, amount, note.strip()),
    )


def delete_prepayment(db, loan_id, prepay_id):
    db.execute("DELETE FROM loan_prepayments WHERE id=? AND loan_id=?", (prepay_id, loan_id))


def load(db, loan, today):
    """Everything the pages need for one loan."""
    emi_rows = db.execute(
        "SELECT id, start_date, amount FROM loan_emis WHERE loan_id=? ORDER BY start_date", (loan["id"],)
    ).fetchall()
    rate_rows = db.execute(
        "SELECT id, start_date, rate FROM loan_rates WHERE loan_id=? ORDER BY start_date", (loan["id"],)
    ).fetchall()
    prepay_rows = db.execute(
        "SELECT id, date, amount, note FROM loan_prepayments WHERE loan_id=? ORDER BY date, id",
        (loan["id"],),
    ).fetchall()

    emis = [(r["start_date"], r["amount"]) for r in emi_rows]
    rates = [(r["start_date"], r["rate"]) for r in rate_rows]
    prepays = [(r["date"], r["amount"]) for r in prepay_rows]
    recorded = [
        {"due": r["due_date"], "emi": r["emi"], "principal": r["principal"],
         "interest": r["interest"], "prepaid": r["prepaid"],
         "capitalized": r["capitalized"], "closing": r["closing"]}
        for r in db.execute(
            "SELECT * FROM loan_installments WHERE loan_id=? ORDER BY due_date", (loan["id"],))
    ]
    rows = build_schedule(loan["principal"], loan["start_date"], emis, rates, prepays, today, recorded,
                          loan["rate_applies"])
    return {
        "loan": dict(loan),
        "rows": rows,
        "summary": summarize(loan, rows, emis, rates, prepays, today),
        "emi_timeline": timeline([(r["start_date"], r["amount"], r["id"]) for r in emi_rows], today),
        "rate_timeline": timeline([(r["start_date"], r["rate"], r["id"]) for r in rate_rows], today),
        "prepayments": [
            {"id": r["id"], "date": parse_date(r["date"]), "amount": r["amount"], "note": r["note"]}
            for r in prepay_rows
        ],
    }
