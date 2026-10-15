"""Import months (salary, invested, expenses) and investment snapshots from a
JSON file that Claude makes from whatever records the user has: bank
statements, a spreadsheet laid out differently, another app's export.

Same shape as the loan import: `build_prompt()` is what the user gives Claude,
`parse()` validates the file it returns, `describe()` shows what would change,
`create()` stores it. Existing months are updated, never emptied: categories
the file doesn't mention are left alone.
"""
import json
import re
from datetime import date

from . import database as d
from .loan_import import Check, extract_json

FORMAT = "hisaab-data-import"
VERSION = 1
MAX_MONTHS = 600
MAX_CATEGORIES = 100
INVESTMENT_FIELDS = ("india_value", "india_profit", "germany_value", "germany_profit", "eur_inr_rate")


PROMPT_TEMPLATE = """\
I'm moving my money records into Hisaab, a personal finance tracker that works month by month. My records are in some other form: bank or card statements, a spreadsheet laid out differently, another app's export, or notes. I've attached them. Read them and turn them into ONE JSON file in exactly the format below, which Hisaab will read.

Today's date is {today}.

HOW WE'LL WORK
Step 1: read first, then ask me. Records look different from person to person. Read everything, tell me briefly what you found, and ASK ME about anything you can't determine with confidence before you write the file. Put all your questions in ONE numbered message and wait for my answers. Things worth asking about:
  - which months to include, and whether the first or last month is incomplete;
  - the currency. Hisaab records salary, expenses and investments-per-month in euros. If my documents are in another currency, ask which rate to use rather than converting silently;
  - which incoming payments are my salary, and which are refunds, transfers or other income;
  - transfers between my own accounts (these are neither income nor expense: leave them out);
  - which outgoing payments are money I moved into savings or investments (these go in "invested", not in expenses);
  - how to group spending into categories, and a list of the specific payments you can't place (with date and amount) so I can tell you where they belong;
  - for investment values: which date each month's value is from, which currency each is in, and how profit is defined;
  - anything unreadable, missing or contradicting another document.
If the documents already answer everything, say so and go straight to the file.
Step 2: once I've answered, create the file.

HOW TO DELIVER THE FILE
- Create a downloadable file named hisaab-data.json containing only the JSON object. If you can't create files, give me the JSON in a single code block instead.
- After the JSON, list every assumption you made so I can check it.

RULES
1. Never guess or invent numbers. If something isn't in the documents, ask me. Use null only for a value I've told you doesn't apply or that I don't know.
2. Amounts are plain positive numbers: no currency symbols, no thousands separators, no quotes. Only profit figures may be negative.
3. Add up individual payments: one entry per category per month, not one per payment. Take refunds off the category they belong to.
4. Do not include my name, address, account numbers or any other personal identifiers.
5. Use my existing category names (listed below) whenever a payment clearly fits one. Add a new category only when none fits, and keep new names short and consistent.
6. Dates are year and month numbers only (for example year 2026, month 3).

MY EXISTING CATEGORIES
{categories}

WHAT EACH FIELD MEANS
- as_of: the date the records are accurate to.
- months: one entry per calendar month, oldest first. Include a month only if the documents cover it.
    year, month: the calendar month (month is 1 to 12).
    salary: salary received that month, in euros (what actually reached my account). null if unknown.
    invested: euros I moved into investments or savings that month. null if unknown.
    expenses: one entry per category, with the total spent that month in euros.
- investments: optional month-end snapshots of what my investments are worth. Leave the list empty if I have none to record. Per entry:
    year, month
    india_value: value of my investments held in India, in rupees. null if none or unknown.
    india_profit: total profit or loss on them so far, in rupees (negative if a loss). null if unknown.
    germany_value: value of my investments held in Germany, in euros. null if none or unknown.
    germany_profit: total profit or loss on them so far, in euros. null if unknown.
    eur_inr_rate: rupees per euro used that month. null if unknown.

FORMAT (these values are only an example; replace them with the real ones)
{example}

BEFORE YOU ANSWER, CHECK
- The JSON is valid, and each month appears only once.
- Each month's expenses add up to what the documents show, with transfers between my own accounts left out.
- Salary and invested are in euros, and no payment is counted twice (for example once as an expense and again as invested).
"""

EXAMPLE = {
    "format": FORMAT,
    "version": VERSION,
    "as_of": "2026-03-31",
    "months": [
        {"year": 2026, "month": 2, "salary": 3000, "invested": 800,
         "expenses": [{"category": "Rent", "amount": 900}, {"category": "Groceries", "amount": 310.5},
                      {"category": "Transport", "amount": 49}]},
        {"year": 2026, "month": 3, "salary": 3000, "invested": 800,
         "expenses": [{"category": "Rent", "amount": 900}, {"category": "Groceries", "amount": 287.2}]},
    ],
    "investments": [
        {"year": 2026, "month": 3, "india_value": 410000, "india_profit": 52000,
         "germany_value": 9800, "germany_profit": 420, "eur_inr_rate": 108.5},
    ],
}


def build_prompt(today: date, categories=()) -> str:
    listed = "\n".join(f"- {c}" for c in categories) if categories else "(none yet: choose short, sensible names)"
    return PROMPT_TEMPLATE.format(today=today.isoformat(), categories=listed,
                                  example=json.dumps(EXAMPLE, indent=2))


def _clean_category(name) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:60]


def parse(text: str, today: date, known_categories=()) -> dict:
    """Validate an import file. Returns {"ok", "errors", "warnings", "info",
    "data"}; `data` is only set when there are no errors."""
    check = Check()
    result = {"ok": False, "errors": check.errors, "warnings": check.warnings,
              "info": check.info, "data": None}

    raw, problem = extract_json(text)
    if problem:
        check.error(problem)
        return result
    if not isinstance(raw, dict):
        check.error("The file should contain one JSON object.")
        return result
    if raw.get("format") != FORMAT:
        check.error(f'Missing the marker "format": "{FORMAT}". Is this a file made with the Hisaab import prompt?')
        return result
    if raw.get("version") != VERSION:
        check.error(f"Unsupported file version {raw.get('version')!r} (expected {VERSION}).")
        return result

    canonical = {c.lower(): c for c in known_categories}
    renamed = {}

    def year_month(item, label):
        year = check.number(item.get("year"), f"{label} year", minimum=2000, maximum=2100)
        month = check.number(item.get("month"), f"{label} month", minimum=1, maximum=12)
        if year is None or month is None:
            return None
        if year != int(year) or month != int(month):
            check.error(f"{label}: year and month must be whole numbers.")
            return None
        return int(year), int(month)

    # --- months
    months_in = raw.get("months") or []
    if not isinstance(months_in, list):
        check.error('"months" must be a list.')
        months_in = []
    if len(months_in) > MAX_MONTHS:
        check.error(f"Too many months ({len(months_in)}); the limit is {MAX_MONTHS}.")
        months_in = []
    months, seen = {}, set()
    for i, item in enumerate(months_in, 1):
        if not isinstance(item, dict):
            check.error(f"months #{i}: expected an object.")
            continue
        ym = year_month(item, f"months #{i}")
        if ym is None:
            continue
        label = f"{ym[0]}-{ym[1]:02d}"
        if ym in seen:
            check.error(f"{label} appears more than once. Put each month in a single entry.")
            continue
        seen.add(ym)
        salary = check.number(item.get("salary"), f"{label} salary", minimum=0, required=False)
        invested = check.number(item.get("invested"), f"{label} invested", minimum=0, required=False)

        expenses_in = item.get("expenses") or []
        if isinstance(expenses_in, dict):               # {"Rent": 700} is fine too
            expenses_in = [{"category": k, "amount": v} for k, v in expenses_in.items()]
        if not isinstance(expenses_in, list):
            check.error(f"{label} expenses must be a list.")
            expenses_in = []
        expenses, merged = {}, 0
        for j, line in enumerate(expenses_in, 1):
            if not isinstance(line, dict):
                check.error(f"{label} expenses #{j}: expected an object.")
                continue
            category = _clean_category(line.get("category"))
            amount = check.number(line.get("amount"), f"{label} expenses #{j} amount", minimum=0)
            if not category:
                check.error(f"{label} expenses #{j}: a category name is required.")
                continue
            if amount is None:
                continue
            mapped = canonical.get(category.lower())
            if mapped is None:
                canonical[category.lower()] = category
            elif mapped != category:
                renamed[category] = mapped
                category = mapped
            if category in expenses:
                merged += 1
            expenses[category] = round(expenses.get(category, 0.0) + amount, 2)
        if len(expenses) > MAX_CATEGORIES:
            check.error(f"{label}: more than {MAX_CATEGORIES} categories.")
        if merged:
            check.info.append(f"{label}: {merged} repeated categor{'y' if merged == 1 else 'ies'} added together.")
        if salary is None and invested is None and not expenses:
            check.warn(f"{label} has no salary, investment or expenses and will be skipped.")
            continue
        months[ym] = {"year": ym[0], "month": ym[1], "salary": salary, "invested": invested,
                      "expenses": expenses}

    # --- investment snapshots
    inv_in = raw.get("investments") or []
    if not isinstance(inv_in, list):
        check.error('"investments" must be a list.')
        inv_in = []
    investments, seen_inv = {}, set()
    for i, item in enumerate(inv_in, 1):
        if not isinstance(item, dict):
            check.error(f"investments #{i}: expected an object.")
            continue
        ym = year_month(item, f"investments #{i}")
        if ym is None:
            continue
        label = f"investments {ym[0]}-{ym[1]:02d}"
        if ym in seen_inv:
            check.error(f"{label} appears more than once.")
            continue
        seen_inv.add(ym)
        row = {"year": ym[0], "month": ym[1]}
        for field in INVESTMENT_FIELDS:
            profit = field.endswith("profit")
            row[field] = check.number(item.get(field), f"{label} {field}",
                                      minimum=None if profit else 0, required=False)
        if all(row[f] is None for f in INVESTMENT_FIELDS):
            check.warn(f"{label} has no values and will be skipped.")
            continue
        investments[ym] = row

    if check.errors:
        return result
    if not months and not investments:
        check.error("The file has no months and no investments to import.")
        return result

    if renamed:
        shown = ", ".join(f"'{a}' → '{b}'" for a, b in list(renamed.items())[:4])
        check.info.append(f"Matched {len(renamed)} category name(s) to ones you already use: {shown}"
                          f"{'…' if len(renamed) > 4 else ''}.")
    for ym, m in sorted(months.items()):
        if m["salary"] == 0 and m["expenses"]:
            check.warn(f"{ym[0]}-{ym[1]:02d}: salary is 0 but there are expenses. Is the salary missing?")
    next_month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    ahead = sorted(ym for ym in months if ym > next_month)
    if ahead:
        check.warn(f"{len(ahead)} month(s) are more than a month ahead of today "
                   f"(for example {ahead[0][0]}-{ahead[0][1]:02d}). Check the year.")

    result["data"] = {"months": [months[k] for k in sorted(months)],
                      "investments": [investments[k] for k in sorted(investments)]}
    result["ok"] = True
    return result


# ---------- what would change ----------

def describe(db, data) -> dict:
    """Compare the file with what's stored, for the review page."""
    months, new_months, lines, notes = [], 0, 0, []
    for m in data["months"]:
        existing = d.get_month(db, m["year"], m["month"])
        was = {}
        if existing:
            was = {e["category"].lower(): e["amount"] for e in d.month_expenses(db, existing["id"])}
        else:
            new_months += 1
        rows = [{"category": c, "amount": a, "was": was.get(c.lower())}
                for c, a in sorted(m["expenses"].items(), key=lambda kv: -kv[1])]
        lines += len(rows)
        if not existing and m["salary"] is None:
            notes.append(f"{m['year']}-{m['month']:02d} is a new month with no salary, so it will show as 0 "
                         "until you add one.")
        months.append({**m, "status": "update" if existing else "new",
                       "was_salary": existing["salary"] if existing else None,
                       "was_invested": existing["invested"] if existing else None,
                       "lines": rows, "total": sum(m["expenses"].values())})

    latest = db.execute("SELECT eur_inr_rate FROM investments ORDER BY year DESC, month DESC LIMIT 1").fetchone()
    fallback = latest["eur_inr_rate"] if latest else 110.0
    investments = []
    for inv in data["investments"]:
        existing = db.execute("SELECT * FROM investments WHERE year=? AND month=?",
                              (inv["year"], inv["month"])).fetchone()
        if inv["eur_inr_rate"] is None and not existing:
            notes.append(f"{inv['year']}-{inv['month']:02d}: no EUR→INR rate given; your latest rate "
                         f"({fallback:g}) will be used. You can edit it afterwards.")
        investments.append({**inv, "status": "update" if existing else "new"})
    return {"months": months, "investments": investments, "new_months": new_months,
            "updated_months": len(months) - new_months, "expense_lines": lines,
            "total_expenses": sum(m["total"] for m in months), "notes": notes}


# ---------- storage ----------

def create(db, data) -> dict:
    for m in data["months"]:
        month_id = d.upsert_month(db, m["year"], m["month"], salary=m["salary"], invested=m["invested"])
        for category, amount in m["expenses"].items():
            d.set_expense(db, month_id, category, amount)

    latest = db.execute("SELECT eur_inr_rate FROM investments ORDER BY year DESC, month DESC LIMIT 1").fetchone()
    fallback = latest["eur_inr_rate"] if latest else 110.0
    for inv in data["investments"]:
        rate = inv["eur_inr_rate"]
        db.execute(
            """INSERT INTO investments
                   (year, month, india_value, india_profit, germany_value, germany_profit, eur_inr_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(year, month) DO UPDATE SET
                   india_value    = COALESCE(?, investments.india_value),
                   india_profit   = COALESCE(?, investments.india_profit),
                   germany_value  = COALESCE(?, investments.germany_value),
                   germany_profit = COALESCE(?, investments.germany_profit),
                   eur_inr_rate   = COALESCE(?, investments.eur_inr_rate)""",
            (inv["year"], inv["month"], inv["india_value"], inv["india_profit"], inv["germany_value"],
             inv["germany_profit"], rate if rate is not None else fallback,
             inv["india_value"], inv["india_profit"], inv["germany_value"], inv["germany_profit"], rate))
    return {"Months": len(data["months"]),
            "Expense entries": sum(len(m["expenses"]) for m in data["months"]),
            "Investment snapshots": len(data["investments"])}
