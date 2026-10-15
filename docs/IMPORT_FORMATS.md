# Import formats

Hisaab can read three kinds of files. You rarely write them by hand: for the two JSON formats you give
**Claude** a prompt (shown on the Import page) plus your documents, and it writes the file. This page is
the reference for what the files contain and how they're checked, useful if you want to understand an
error message, write a file yourself, or use another tool.

**Contents**
[The Excel workbook](#the-excel-workbook) ·
[Months and investments (JSON)](#months-and-investments-json) ·
[A running loan (JSON)](#a-running-loan-json) ·
[How a file is checked](#how-a-file-is-checked)

---

## The Excel workbook

For the original "Salary Plan" layout. Upload it on **Import → I have the Salary Plan spreadsheet**, or run
`python import_excel.py "Salary Plan.xlsx" [username]` (without a username it imports into the first
account's data).

**Year sheets**: one sheet per year, named after the year (`2026`):

| Row | Column A | Columns B, C, … |
|---|---|---|
| 1 | `Total Salary` | the salary for each month |
| 2 | `Expenses` | the month names: `January`, `February`, … |
| 3 and on | a category name (`Rent`, `Grocery`, …) | the amount for each month (empty cells are skipped) |
| optional | `Can Invest` | the amount invested each month |

- If the months wrap past December (…`Nov`, `Dec`, `Jan`), the wrapped months go into the *next* year.
- Rows named `Total Expense`, `Salary Remaining…` and `Remainder…` are ignored (Hisaab works them out).

**Investment sheet**: any sheet whose name contains "Investment":

| Row | Column A | Columns B, C, … |
|---|---|---|
| 1 | anything | month names (assigned to the latest year sheet's year) |
| labelled rows | `India Investment Value (Rupees)`, `India Profit (Rupees)`, `Germany Investment Value (Euros)`, `Germany Profit (Euros)`, `EUR to INR rate` | the values |

Net worth and profit percentages are recomputed, so those rows are ignored.
**Months that already exist are overwritten** with the sheet's values.

---

## Months and investments (JSON)

Made with the prompt under **Import → My records are in some other form**. Uploaded at
`/import/assisted`, reviewed, then confirmed. Salary, expenses and "invested" are in **euros**.

```json
{
  "format": "hisaab-data-import",
  "version": 1,
  "as_of": "2026-03-31",
  "months": [
    {
      "year": 2026,
      "month": 2,
      "salary": 3000,
      "invested": 800,
      "expenses": [
        {
          "category": "Rent",
          "amount": 900
        },
        {
          "category": "Groceries",
          "amount": 310.5
        },
        {
          "category": "Transport",
          "amount": 49
        }
      ]
    },
    {
      "year": 2026,
      "month": 3,
      "salary": 3000,
      "invested": 800,
      "expenses": [
        {
          "category": "Rent",
          "amount": 900
        },
        {
          "category": "Groceries",
          "amount": 287.2
        }
      ]
    }
  ],
  "investments": [
    {
      "year": 2026,
      "month": 3,
      "india_value": 410000,
      "india_profit": 52000,
      "germany_value": 9800,
      "germany_profit": 420,
      "eur_inr_rate": 108.5
    }
  ]
}
```

### Fields

| Field | Meaning |
|---|---|
| `format` | Must be `"hisaab-data-import"` |
| `version` | Must be `1` |
| `as_of` | Optional date the records are accurate to (informational) |
| `months[]` | One entry per calendar month; each month at most once |
| `months[].year`, `.month` | Whole numbers; year 2000–2100, month 1–12 |
| `months[].salary` | Salary received that month, ≥ 0, or `null` if unknown |
| `months[].invested` | Euros moved into investments/savings that month, ≥ 0, or `null` |
| `months[].expenses[]` | `{"category": "Rent", "amount": 900}`, one per category. A plain `{"Rent": 900}` object also works |
| `investments[]` | Optional month-end snapshots; each month at most once |
| `investments[].india_value` / `.india_profit` | Value of holdings in India and total profit, in rupees (profit may be negative) |
| `investments[].germany_value` / `.germany_profit` | Same, in euros |
| `investments[].eur_inr_rate` | Rupees per euro that month, or `null` |

Any investment field may be `null`; only the fields present are changed.

### What happens when it's imported

- **Existing months are updated, never emptied.** A category the file doesn't mention keeps its value.
  A `null` salary or invested leaves the stored one alone. A new month with no salary shows as 0
  (the review page warns you).
- **Category names are matched to yours**, ignoring case (`groceries` → your `Groceries`), so you don't end
  up with near-duplicates. A category repeated within a month is added together.
- **Investment rows are partial updates.** A new row with no rate uses your latest saved rate.
- Expenses, salary, invested and values can't be negative. Net refunds off the right category instead.
- Limits: 600 months, 100 categories per month, category names up to 60 characters, 1 MB per file.

---

## A running loan (JSON)

Made with the prompt under **Loans → Import a running loan**. This one is designed for loans that have
been going for a while: it carries the lender's **actual past installments**, which Hisaab keeps exactly
as recorded and then projects forward from the last one. That's what makes loans with interest-only
periods or unpaid interest that was added to the principal come out right.

```json
{
  "format": "hisaab-loan-import",
  "version": 1,
  "as_of": "2026-03-05",
  "loan": {
    "name": "Home loan",
    "lender": "Example Bank",
    "currency": "INR",
    "principal": 500000,
    "start_date": "2025-12-05",
    "tenure_months": 60,
    "rate_applies": "on_emi_date",
    "notes": "Account no. 123456. Sanctioned 600000."
  },
  "emi_history": [
    {
      "from": "2025-12-05",
      "amount": 10600
    }
  ],
  "rate_history": [
    {
      "from": "2025-12-05",
      "rate": 9.5
    }
  ],
  "prepayments": [
    {
      "date": "2026-02-10",
      "amount": 20000,
      "note": "bonus"
    }
  ],
  "installments": [
    {
      "due": "2026-01-05",
      "emi": 10600,
      "principal": 6640,
      "interest": 3960,
      "closing_balance": 493360
    },
    {
      "due": "2026-02-05",
      "emi": 10600,
      "principal": 6693,
      "interest": 3907,
      "closing_balance": 486667
    },
    {
      "due": "2026-03-05",
      "emi": 10600,
      "principal": 6746,
      "interest": 3854,
      "closing_balance": 459921
    }
  ]
}
```

### Fields

| Field | Meaning |
|---|---|
| `format`, `version` | `"hisaab-loan-import"` and `1` |
| `as_of` | The date the documents are accurate to (the statement date). Installments after it are ignored |
| `loan.name` | A short name (up to 120 characters) |
| `loan.lender` | The lender, or `""` |
| `loan.currency` | One of `EUR INR USD GBP CHF AED CAD AUD` |
| `loan.principal` | The amount actually **disbursed**, not the sanctioned limit (put that in `notes`) |
| `loan.start_date` | Due date of the first installment in the schedule |
| `loan.tenure_months` | The original total tenure, or `null`. Used to say whether you're ahead of or behind plan |
| `loan.notes` | Free text (up to 2000 characters): account number, sanctioned amount, anything unusual |
| `loan.rate_applies` | Optional: `"on_emi_date"` or `"next_emi"` (see below). Omit if unsure |
| `emi_history[]` | `{"from": date, "amount": number}`: every time the installment amount changed. At least one |
| `rate_history[]` | `{"from": date, "rate": percent per year}`: the rate actually charged, one entry per change. At least one |
| `prepayments[]` | `{"date", "amount", "note"}`: extra payments. May be empty |
| `installments[]` | The lender's rows, oldest first, **only those due on or before `as_of`** (up to 1200). May be empty |
| `installments[].due` | The due date |
| `installments[].emi` | The total amount due/paid that installment |
| `installments[].principal` | The principal part |
| `installments[].interest` | The interest part |
| `installments[].closing_balance` | Outstanding principal after this installment, **exactly as the document shows it** |

`rate_applies` records whether a rate change that takes effect *on* an installment's due date was already
used for that installment (`on_emi_date`) or only from the following one (`next_emi`).

**No schedule, only a current balance?** Send `"installments": []`, set `loan.principal` to the outstanding
principal after your most recent paid installment, and `loan.start_date` to that installment's due date.
Hisaab then tracks the loan from there.

### What Hisaab works out for itself

You don't tell it about interest that was added to the principal or about extra payments. It compares
each row's `closing_balance` with the previous one:

- If the balance **rose** by more than the principal paid explains (more than 2 currency units of slack
  for rounding), the difference is **interest added to the principal** (for example the unpaid interest
  of an interest-only period). It's shown as such and counted in the total interest.
- If it **fell** by more than the principal paid, the difference is treated as an **extra payment**, and the
  review page warns you so you can check it.

---

## How a file is checked

Both JSON importers work in two steps, so nothing is saved by surprise:

1. **Check.** The file is parsed and validated. Errors (the file can't be used) are listed all at once, in
   words. Warnings (usable, but look at this) and notes are shown on the **review page** together with
   what would change.
2. **Confirm.** You press the button; the same text is validated again and then stored.

What the validator accepts leniently, because chat tools aren't perfectly tidy:

- A bare JSON file, a ```` ```json ```` block, or a whole chat reply with the JSON somewhere inside it.
- Numbers written as strings with commas or currency signs (`"1,20,000"`, `"₹5000"`).

What it insists on:

- Dates as `YYYY-MM-DD`. (`11/10/26` is ambiguous between lenders; the prompt tells Claude to convert it.)
- The `format` marker and `version`, so a loan file can't be uploaded as a months file by mistake.
- No duplicate months, installments or conflicting history entries.

Errors name the exact place (`installments #14 due: '11/10/26' is not a valid YYYY-MM-DD date`). Paste
the message back to Claude and ask for a corrected file.
