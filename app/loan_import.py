"""Import a running loan from a JSON file.

The user gives Claude the prompt from `build_prompt()` plus their loan documents
(statement, repayment schedule, rate-change table); Claude returns a JSON file;
`parse()` validates it and `create()` stores the loan. Everything the lender
actually charged up to the statement date is kept as recorded installments, and
Hisaab projects forward from the last one.
"""
import json
import re
from datetime import date

from . import loans as L

FORMAT = "hisaab-loan-import"
VERSION = 1
TOLERANCE = 2.0          # rounding slack when reconciling balances
MAX_ROWS = 1200
MAX_MESSAGES = 12


PROMPT_TEMPLATE = """\
I'm moving an existing loan into Hisaab, my personal finance tracker. I've attached documents about the loan: the repayment schedule or loan statement, a table of interest-rate changes, and possibly a sanction letter. Read them carefully and produce ONE JSON file in exactly the format below. Hisaab will read that file to set the loan up.

Today's date is {today}.

HOW WE'LL WORK
Step 1: read first, then ask me. Every lender lays its documents out differently: different column names, date formats, number formats (such as 12,00,000), interest shown in one column or split across several, part-payment or interest-only phases, floating rates, and so on. Read everything, tell me briefly what you found, and ASK ME about anything you can't determine with confidence before you write the file. Put all your questions in ONE numbered message and wait for my answers. Things worth asking about:
  - what a column means, when it isn't obvious (principal vs interest vs total installment vs balance);
  - the date format, if it is ambiguous;
  - the lender's name and what I want the loan called;
  - the amount actually disbursed, and the original tenure;
  - whether the rate is floating, and whether the rate-change table is complete and up to date;
  - whether there was a part-payment, interest-only or moratorium period, and what happened to the interest that went unpaid;
  - any extra payments or part-prepayments I made that these documents don't show;
  - whether a newer statement exists than the one I attached, if the latest rows look old;
  - anything that is unreadable, missing or contradicts another document.
If the documents already answer everything, say so and go straight to the file.
Step 2: once I've answered, create the file.

HOW TO DELIVER THE FILE
- Create a downloadable file named loan-import.json containing only the JSON object. If you can't create files, give me the JSON in a single code block instead.
- After the JSON, list every assumption you made so I can check it.

RULES
1. Never guess or invent numbers. If a value is not in the documents, ask me for it. Use null only for optional values (loan.lender, loan.tenure_months) that I've told you I don't know. If two documents disagree, ask me which one is right.
2. Dates must be ISO: YYYY-MM-DD. Documents often show DD/MM/YY (for example 11/10/26 is 11 October 2026). If a date is ambiguous, work out the format from context (such as the same day-of-month repeating on every row) and tell me what you decided.
3. Amounts are plain numbers: no currency symbols, no thousands separators, no quotes.
4. Do not include my name, address, phone number or any personal identifiers. The loan account number may go into "notes".

WHAT EACH FIELD MEANS
- as_of: the date the documents are accurate to (the statement or schedule issue date).
- loan.name: a short name such as "Home loan".
- loan.lender: the lender's name if the documents state it, otherwise "".
- loan.currency: one of EUR, INR, USD, GBP, CHF, AED, CAD, AUD.
- loan.principal: the amount actually disbursed. If it differs from the sanctioned amount, mention the sanctioned amount in "notes".
- loan.start_date: the due date of the first installment in the schedule (or the disbursement date if that is all you have).
- loan.tenure_months: the original total tenure, from start_date to the last installment of the original schedule. null if unknown.
- loan.rate_applies (optional): look at the installment rows around a past rate change. If the installment due ON the day a new rate took effect was already charged at the new rate, set "on_emi_date". If it was still charged at the old rate and the new rate started with the following installment, set "next_emi". If you can't tell, or there was no rate change, leave this field out.
- emi_history: the regular installment amount and the date it applies from. Add a new entry every time the installment amount changes. A part-payment or interest-only phase followed by full EMIs is two entries. A change of interest rate alone is NOT a new entry when the amount stays the same.
- rate_history: the annual interest rate in percent that I am actually charged, with the date it took effect. Include the first rate. Use the "rate of interest" column, not the benchmark rate or the spread. One entry per change.
- prepayments: extra payments I made on top of the regular installments (date, amount, short note). Use [] if there are none.
- installments: the lender's actual installment rows, oldest first, ONLY those with a due date on or before as_of. Do not include later (future) rows; Hisaab projects those itself. Per row:
    due: the due date
    emi: the total amount due or paid for that installment
    principal: the principal part of that installment
    interest: the interest part of that installment
    closing_balance: the outstanding principal after that installment, exactly as the document shows it (if unpaid interest was added to the principal at some point, the document's balance will reflect that: copy it, don't correct it)
  If I have no schedule or statement and only know the current balance, use "installments": [], set loan.principal to the outstanding principal after my most recent paid installment, and set loan.start_date to that installment's due date.

FORMAT (these values are only an example; replace them with the real ones)
{example}

BEFORE YOU ANSWER, CHECK
- The JSON is valid and every date is YYYY-MM-DD.
- Installment dates are consecutive months, none missing, none after as_of.
- For each installment, principal + interest = emi, and the previous closing_balance minus principal = closing_balance (differences only where the document itself shows interest being added to the principal or an extra payment).
- emi_history and rate_history are in date order and each starts no later than the first installment.
"""

EXAMPLE = {
    "format": FORMAT,
    "version": VERSION,
    "as_of": "2026-03-05",
    "loan": {
        "name": "Home loan", "lender": "Example Bank", "currency": "INR",
        "principal": 500000, "start_date": "2025-12-05", "tenure_months": 60,
        "rate_applies": "on_emi_date",
        "notes": "Account no. 123456. Sanctioned 600000.",
    },
    "emi_history": [{"from": "2025-12-05", "amount": 10600}],
    "rate_history": [{"from": "2025-12-05", "rate": 9.5}],
    "prepayments": [{"date": "2026-02-10", "amount": 20000, "note": "bonus"}],
    "installments": [
        {"due": "2026-01-05", "emi": 10600, "principal": 6640, "interest": 3960, "closing_balance": 493360},
        {"due": "2026-02-05", "emi": 10600, "principal": 6693, "interest": 3907, "closing_balance": 486667},
        {"due": "2026-03-05", "emi": 10600, "principal": 6746, "interest": 3854, "closing_balance": 459921},
    ],
}


def build_prompt(today: date) -> str:
    return PROMPT_TEMPLATE.format(
        today=today.isoformat(), example=json.dumps(EXAMPLE, indent=2))


# ---------- parsing ----------

def extract_json(text: str):
    """Accept a bare file, a ```json fenced block, or a whole chat reply that
    contains one JSON object. Returns (obj, error)."""
    text = (text or "").strip().lstrip("﻿")
    if not text:
        return None, "The file is empty."
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as exc:
            last_error = exc
    return None, f"This isn't valid JSON ({last_error.msg} at line {last_error.lineno}, column {last_error.colno})."


class Check:
    """Collects problems so the user sees all of them at once."""

    def __init__(self):
        self.errors, self.warnings, self.info = [], [], []

    def error(self, msg):
        if len(self.errors) < MAX_MESSAGES:
            self.errors.append(msg)

    def warn(self, msg):
        if len(self.warnings) < MAX_MESSAGES:
            self.warnings.append(msg)

    def date(self, value, label, required=True):
        if value in (None, ""):
            if required:
                self.error(f"{label}: a date is required.")
            return None
        try:
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
                return date.fromisoformat(value.strip())
        except ValueError:
            pass
        self.error(f"{label}: '{value}' is not a valid YYYY-MM-DD date.")
        return None

    def number(self, value, label, minimum=None, maximum=None, required=True):
        if value in (None, ""):
            if required:
                self.error(f"{label}: a number is required.")
            return None
        if isinstance(value, str):
            value = re.sub(r"[,\s₹€$£]", "", value)
        try:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
        except (TypeError, ValueError):
            self.error(f"{label}: '{value}' is not a number.")
            return None
        if minimum is not None and number < minimum:
            self.error(f"{label}: must be at least {minimum:g}.")
            return None
        if maximum is not None and number > maximum:
            self.error(f"{label}: must be at most {maximum:g}.")
            return None
        return number


def _money(value, currency):
    return f"{L.CURRENCIES.get(currency, currency + ' ')}{value:,.0f}"


def parse(text: str, today: date) -> dict:
    """Validate an import file. Returns {"ok", "errors", "warnings", "info",
    "data", "preview"}; `data` is only set when there are no errors."""
    check = Check()
    result = {"ok": False, "errors": check.errors, "warnings": check.warnings,
              "info": check.info, "data": None, "preview": None}

    raw, problem = extract_json(text)
    if problem:
        check.error(problem)
        return result
    if not isinstance(raw, dict):
        check.error("The file should contain one JSON object.")
        return result
    if raw.get("format") != FORMAT:
        check.error(f'Missing the marker "format": "{FORMAT}". Is this a file made with the Hisaab loan prompt?')
        return result
    if raw.get("version") != VERSION:
        check.error(f"Unsupported file version {raw.get('version')!r} (expected {VERSION}).")
        return result

    # --- loan
    src = raw.get("loan")
    if not isinstance(src, dict):
        check.error('"loan" is missing.')
        return result
    name = str(src.get("name") or "").strip()
    if not name:
        check.error("loan.name: a name is required.")
    currency = str(src.get("currency") or "").strip().upper()
    if currency not in L.CURRENCIES:
        check.error(f"loan.currency: '{src.get('currency')}' is not supported "
                    f"(use one of {', '.join(L.CURRENCIES)}).")
    principal = check.number(src.get("principal"), "loan.principal", minimum=0.01)
    start = check.date(src.get("start_date"), "loan.start_date")
    tenure = None
    if src.get("tenure_months") not in (None, ""):
        t = check.number(src.get("tenure_months"), "loan.tenure_months", minimum=1, maximum=L.MAX_MONTHS)
        tenure = int(t) if t is not None else None
    rate_applies = str(src.get("rate_applies") or "on_emi_date").strip()
    if rate_applies not in L.RATE_TIMINGS:
        check.error(f"loan.rate_applies: '{rate_applies}' should be \"on_emi_date\" or \"next_emi\".")
    loan = {"name": name[:120], "lender": str(src.get("lender") or "").strip()[:120],
            "rate_applies": rate_applies,
            "currency": currency, "principal": principal,
            "start_date": start.isoformat() if start else None, "tenure_months": tenure,
            "notes": str(src.get("notes") or "").strip()[:2000]}

    # --- EMI and rate histories
    def history(key, value_key, label, minimum, maximum):
        items = raw.get(key)
        if not isinstance(items, list) or not items:
            check.error(f'"{key}" needs at least one entry.')
            return []
        out = {}
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict):
                check.error(f"{key} #{i}: expected an object.")
                continue
            when = check.date(item.get("from"), f"{key} #{i} from")
            value = check.number(item.get(value_key), f"{key} #{i} {label}", minimum=minimum, maximum=maximum)
            if when is not None and value is not None:
                if when in out and out[when] != value:
                    check.error(f"{key}: two different values for {when.isoformat()}.")
                out[when] = value
        return sorted(out.items())

    emis = history("emi_history", "amount", "amount", 0.01, None)
    rates = history("rate_history", "rate", "rate", 0, 100)

    # --- prepayments
    prepayments = []
    for i, item in enumerate(raw.get("prepayments") or [], 1):
        if not isinstance(item, dict):
            check.error(f"prepayments #{i}: expected an object.")
            continue
        when = check.date(item.get("date"), f"prepayments #{i} date")
        amount = check.number(item.get("amount"), f"prepayments #{i} amount", minimum=0.01)
        if when is not None and amount is not None:
            prepayments.append({"date": when.isoformat(), "amount": amount,
                                "note": str(item.get("note") or "").strip()[:200]})

    # --- installments (the lender's actual history)
    as_of = check.date(raw.get("as_of"), "as_of", required=False)
    rows_in = raw.get("installments") or []
    if not isinstance(rows_in, list):
        check.error('"installments" must be a list.')
        rows_in = []
    if len(rows_in) > MAX_ROWS:
        check.error(f"Too many installments ({len(rows_in)}); the limit is {MAX_ROWS}.")
        rows_in = []
    parsed_rows = {}
    for i, item in enumerate(rows_in, 1):
        if not isinstance(item, dict):
            check.error(f"installments #{i}: expected an object.")
            continue
        label = f"installments #{i}"
        due = check.date(item.get("due"), f"{label} due")
        emi = check.number(item.get("emi"), f"{label} emi", minimum=0)
        prin = check.number(item.get("principal"), f"{label} principal", minimum=0)
        intr = check.number(item.get("interest"), f"{label} interest", minimum=0)
        closing = check.number(item.get("closing_balance"), f"{label} closing_balance", minimum=0)
        if None in (due, emi, prin, intr, closing):
            continue
        if due in parsed_rows:
            check.error(f"{label}: {due.isoformat()} appears twice.")
        parsed_rows[due] = {"due": due, "emi": emi, "principal": prin, "interest": intr, "closing": closing}

    if check.errors:
        return result

    dates = sorted(parsed_rows)
    if as_of is None and dates:
        as_of = dates[-1]
    if as_of is not None and as_of > today:
        check.warn(f"as_of ({as_of.isoformat()}) is in the future; treating it as today.")
        as_of = today
    dropped = [d for d in dates if as_of and d > as_of]
    if dropped:
        check.warn(f"{len(dropped)} installment(s) after as_of ({as_of.isoformat()}) were ignored: "
                   "Hisaab projects those itself.")
        dates = [d for d in dates if d <= as_of]

    # reconcile balances, inferring extra payments and capitalised interest
    installments, prev_closing = [], principal
    capitalised_events, prepaid_rows, mismatched = [], [], []
    for d in dates:
        r = parsed_rows[d]
        expected = prev_closing - r["principal"]
        diff = r["closing"] - expected
        prepaid = capitalized = 0.0
        if diff > TOLERANCE:
            capitalized = diff
            capitalised_events.append((d, diff))
        elif diff < -TOLERANCE:
            prepaid = -diff
            prepaid_rows.append((d, prepaid))
        if abs(r["emi"] - (r["principal"] + r["interest"])) > TOLERANCE:
            mismatched.append(d)
        installments.append({"due": d.isoformat(), "emi": r["emi"], "principal": r["principal"],
                             "interest": r["interest"], "prepaid": prepaid,
                             "capitalized": capitalized, "closing": r["closing"]})
        prev_closing = r["closing"]

    for d, amount in capitalised_events:
        check.info.append(
            f"On {d.isoformat()}, {_money(amount, currency)} was added to the principal "
            "(for example interest that went unpaid during a part-payment period).")
    if prepaid_rows:
        shown = ", ".join(f"{d.isoformat()} ({_money(a, currency)})" for d, a in prepaid_rows[:4])
        check.warn(f"The balance fell by more than the principal paid on {len(prepaid_rows)} installment(s): {shown}"
                   f"{'…' if len(prepaid_rows) > 4 else ''}. Treated as extra payments; check the file if that's wrong.")
    if mismatched:
        shown = ", ".join(d.isoformat() for d in mismatched[:4])
        check.warn(f"principal + interest doesn't equal the installment amount on {len(mismatched)} row(s): "
                   f"{shown}{'…' if len(mismatched) > 4 else ''}.")
    gaps = [(a, b) for a, b in zip(dates, dates[1:]) if L.months_between(a, b) != 1]
    if gaps:
        shown = ", ".join(f"{a.isoformat()} → {b.isoformat()}" for a, b in gaps[:3])
        check.warn(f"Installments aren't consecutive months ({shown}). Missing rows make the history incomplete.")
    if dates and dates[0] < date.fromisoformat(loan["start_date"]):
        check.warn("The first installment is dated before loan.start_date.")
    if dates and (today - dates[-1]).days > 75:
        check.info.append(
            f"The newest installment is {dates[-1].isoformat()}. Hisaab treats everything after it as projected "
            "from your EMI and rate history; a newer statement would make it more accurate.")

    data = {"loan": loan, "emis": [(d.isoformat(), v) for d, v in emis],
            "rates": [(d.isoformat(), v) for d, v in rates],
            "prepayments": prepayments, "installments": installments,
            "as_of": as_of.isoformat() if as_of else None}
    result["data"] = data
    result["ok"] = True
    result["preview"] = _preview(data, today)
    return result


def _preview(data, today):
    """What the loan will look like once created (uses the real engine)."""
    loan = dict(data["loan"], id=0)
    rows = L.build_schedule(loan["principal"], loan["start_date"], data["emis"], data["rates"],
                            [(p["date"], p["amount"]) for p in data["prepayments"]], today,
                            data["installments"], loan["rate_applies"])
    s = L.summarize(loan, rows, data["emis"], data["rates"],
                    [(p["date"], p["amount"]) for p in data["prepayments"]], today)
    inst = data["installments"]
    ahead = [r for r in rows if not r["recorded"]]
    return {
        "statement_closing": inst[-1]["closing"] if inst else None,
        "projected_paid": sum(1 for r in ahead if r["paid"]),
        "interest_ahead": sum(r["interest"] for r in ahead),
        "installments": len(inst),
        "first_due": inst[0]["due"] if inst else None,
        "last_due": inst[-1]["due"] if inst else None,
        "emi_entries": len(data["emis"]), "rate_entries": len(data["rates"]),
        "prepayments": len(data["prepayments"]),
        "interest_recorded": sum(r["interest"] for r in inst),
        "capitalized": sum(r["capitalized"] for r in inst),
        "summary": s,
    }


# ---------- storage ----------

def create(db, data) -> int:
    ln = data["loan"]
    loan_id = L.create_loan(db, ln["name"], ln["lender"], ln["currency"], ln["principal"],
                            ln["start_date"], ln["tenure_months"], ln["notes"], None, None,
                            ln["rate_applies"])
    for when, amount in data["emis"]:
        L.set_emi(db, loan_id, when, amount)
    for when, rate in data["rates"]:
        L.set_rate(db, loan_id, when, rate)
    for p in data["prepayments"]:
        L.add_prepayment(db, loan_id, p["date"], p["amount"], p["note"])
    L.add_installments(db, loan_id, data["installments"])
    return loan_id
