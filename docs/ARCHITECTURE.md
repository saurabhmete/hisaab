# Architecture

How Hisaab works inside. For using it see the [user guide](USER_GUIDE.md); for running it see
[deployment](DEPLOYMENT.md).

**Contents**
[Overview](#overview) ·
[A request, start to finish](#a-request-start-to-finish) ·
[Where the data lives](#where-the-data-lives) ·
[Security model](#security-model) ·
[The money pages](#the-money-pages) ·
[The loan engine](#the-loan-engine) ·
[Imports](#imports) ·
[The front end](#the-front-end) ·
[Tests](#tests) ·
[Decisions and limits](#decisions-and-known-limits) ·
[Extending it](#extending-it)

---

## Overview

```
 browser ──HTTPS──▶ Tailscale (serve / funnel) ──▶ container :8000
                                                     │
                                       FastAPI app (uvicorn, one process)
                                                     │
                    ┌────────────────────────────────┼─────────────────────────┐
                    │                                │                         │
          accounts database                 per-person databases        ECB rate (optional)
          data/hisaab.db                    hisaab.db (first account)   api.frankfurter.dev
          users, sessions,                  data/users/<id>.db (others)
          login attempts, meta              months, expenses, investments, loans…
```

A single Python process, server-rendered HTML (Jinja), SQLite files on disk, a little JavaScript. There's no
background worker, no queue, no build step. FastAPI and uvicorn serve the pages; the standard-library
`sqlite3` module talks to the files. Five dependencies in total (`fastapi`, `uvicorn`, `jinja2`,
`python-multipart`, `openpyxl`).

## A request, start to finish

1. **Middleware** (`AuthMiddleware` in `app/main.py`) runs first. `/login`, `/setup` and `/static` are open.
   For everything else it opens the **accounts** database, looks up the session cookie, and if there's no valid
   session redirects to `/login` (or to `/setup` when no account exists yet).
2. **It chooses the data file.** From the logged-in user's id it works out which database file holds *their*
   data and stores that path in a per-request context variable (`database.set_data_path`).
3. **The route runs.** Every data query uses `database.get_db()`, which opens whichever file the middleware
   chose. There is no way to ask for "all users' data".
4. **A template is rendered** with the results. Money pages are plain server-rendered HTML; JavaScript only
   adds charts, live calculation in the planner, tabs and the theme switch.

`get_db()` has no default: if nothing is selected it raises. A bug in the middleware therefore makes the app
fail loudly instead of quietly showing someone else's data.

## Where the data lives

### Two kinds of database

| | File | Holds |
|---|---|---|
| **Accounts** | `data/hisaab.db` | `users`, `sessions`, `login_failures`, `meta` |
| **A person's data** | `data/hisaab.db` for the **first** account; `data/users/<id>.db` for everyone else | `months`, `expenses`, `investments`, `loans`, `loan_emis`, `loan_rates`, `loan_prepayments`, `loan_installments` |

The first account created owns the original file (the `meta` table records it as `data_owner`), which is how
installations from before accounts were separated upgrade with **no data migration**: their data was already in
`hisaab.db`, and it simply stays with their admin. Every later account gets its own file, created empty the first
time they sign in.

Why separate files rather than a `user_id` column on every table? **Isolation by construction.** With a column,
every query anywhere must remember the filter, and one forgotten `WHERE` leaks money. With separate files there
is nothing to forget: a request can only open the file chosen for it. The cost is small: backups must cover the
extra files ([deployment](DEPLOYMENT.md#backups)), and a person's file is deleted when they are removed.

If login is switched off (`HISAAB_AUTH=off`) everybody is a pseudo-user `0` and uses `hisaab.db`.

### The tables

**Accounts**

| Table | Columns |
|---|---|
| `users` | `id`, `username` (unique, case-insensitive), `password_hash`, `is_admin`, `created_at` |
| `sessions` | `token` (primary key), `user_id` → `users` (cascade), `created_at` |
| `login_failures` | `id`, `ip`, `at` |
| `meta` | `key`, `value` (holds `data_owner`) |

**A person's data**

| Table | Columns / notes |
|---|---|
| `months` | `year`, `month`, `salary`, `invested`; unique per (year, month) |
| `expenses` | `month_id` → `months` (cascade), `category`, `amount`; unique per (month, category) |
| `investments` | `year`, `month`, `india_value`, `india_profit`, `germany_value`, `germany_profit`, `eur_inr_rate`; unique per (year, month) |
| `loans` | `name`, `lender`, `currency`, `principal`, `start_date`, `tenure_months`, `notes`, `rate_applies`, `created_at` |
| `loan_emis` | `loan_id`, `start_date`, `amount`; unique per (loan, start date) |
| `loan_rates` | `loan_id`, `start_date`, `rate`; unique per (loan, start date) |
| `loan_prepayments` | `loan_id`, `date`, `amount`, `note` |
| `loan_installments` | The lender's recorded rows: `loan_id`, `due_date`, `emi`, `principal`, `interest`, `prepaid`, `capitalized`, `closing`; unique per (loan, due date) |

Derived numbers (what's left, net worth, outstanding principal…) are **never stored**; they're computed from
these tables each time, so editing any input is always consistent.

### Migrations

There's no migration framework. `init_db()` runs on startup and is **idempotent**: it creates missing tables
(`CREATE TABLE IF NOT EXISTS`), then applies two small in-place upgrades (rebuilding the old two-currency
`loans` table; adding the `rate_applies` column) and records the data owner. Per-person files are created with
the current schema. Rebuilding a table uses SQLite's copy-and-rename with foreign keys off, then verifies
`PRAGMA foreign_key_check`, so history is never lost.

## Security model

**What is protected, and how**

| Concern | Measure |
|---|---|
| Passwords | Salted PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt per user; compared in constant time |
| Sessions | 32-byte random token, stored server-side; cookie is `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS; 90-day lifetime; deleting a user deletes their sessions at once |
| Guessing passwords | 5 failed logins from one address within 15 minutes lock that address out for the rest of the window (HTTP 429). The client address is read through the proxy headers Tailscale sets |
| Claiming the app on first run | `/setup` is locked until you present a random token that is only printed to the server log |
| One person reading another's money | Separate database files per person, selected once per request (see above); admins are not exempt |
| Removing a person | Deletes their account, their sessions and their data file. The first account can't be removed |
| Imports | Files are validated before anything is stored; a review step shows what will change; a size limit of 1 MB |

**What is *not* protected**

- **Data at rest.** The database files are not encrypted. Anyone with access to the Pi (or its SD card, or a
  backup) can read them. This is a home server threat model: protect the machine and the backups.
- **Admins vs the server.** In the app, admins can't see other people's data. Whoever controls the Pi still can.
- **The privacy eye** on the Investments page blurs numbers with CSS for a demo; the numbers are still in the page.
- **No two-factor authentication, no password reset by email** (the app sends no email). The server owner can
  set a password from a terminal (see the [user guide](USER_GUIDE.md#tips-and-troubleshooting)).
- **Funnel exposes the login page to the internet.** Rate limiting helps; strong passwords are the real defence.

## The money pages

- **Overview.** Picks a month (latest by default). *Left* = salary − expenses − invested. The bar splits the
  salary into those three parts. "Spending up/down vs <month>" compares with the previous *recorded* month. The
  investment card values the latest snapshot at the rate it was saved with; the loans card summarises loans that
  still have something to pay.
- **Month planner.** Saving rewrites that month's category list to match the form (so a removed row is
  removed). A *new* month is pre-filled from the most recent earlier month as a suggestion only. EMIs of euro
  loans due that month are offered as one-click rows (`EMI: <loan name>`). The summary panel is plain
  JavaScript recalculating on every keystroke; the server recomputes everything on save.
- **Investments.** Net worth (₹) = India value + Germany value × rate. Profit % = profit ÷ (value − profit).
  The headline cards use the **live ECB rate** (fetched at most every six hours, with a short negative cache,
  and a silent fallback to the saved rate); the table and charts use each month's **saved** rate so history
  doesn't shift when the euro moves.

## The loan engine

All in `app/loans.py`: pure functions that take rows and return rows, tested in isolation.

### The schedule

A loan has a start date, a principal and two independent histories: **EMI** entries (`from` date, amount) and
**rate** entries (`from` date, % per year). The value *in force* on a date is the latest entry starting on or
before it (before the first entry, the first applies). Entries don't store an end date: it's *derived* from the
next entry's start, so history can't overlap or leave gaps. A temporary change ("9% from June to August") is
stored as the change plus an automatic entry that restores the earlier value afterwards.

Installment `i` falls due `i` months after the start date (the day clamped to the month's length). For each:

```
interest   = balance × rate/12/100                 (a full month)
payment    = min(EMI, balance + interest)
principal  = payment − interest
balance    = balance − principal
```

until the balance reaches zero (guarded at 1200 months so an EMI too small to cover the interest can't loop).

**Prepayments** reduce the principal on their date. Interest for that month is split into segments, each
weighted by *days at that balance ÷ days in the month*, so paying early in the month saves more than paying
late.

**When a rate change applies** (`rate_applies`). `on_emi_date`: an installment due on the day a new rate
takes effect already uses it. `next_emi`: it uses the rate in force at the *start* of its period, so the new
rate starts with the following installment. Lenders differ; the loan's Settings says which to use.

### Recorded history

Some loans can't be rebuilt from their terms, such as interest-only periods followed by full EMIs, with the
unpaid interest added to the principal. For these the importer stores the lender's **actual installments**
(`loan_installments`). `build_schedule` takes them as given and projects only from the last one: the first
projected installment is one month after it, starting from its recorded closing balance, and prepayments
dated on or before it are not applied again (they are already in that balance). From the recorded rows the
importer works out any **interest added to the principal** (the balance rose) and any **extra payments** (it
fell by more than the principal paid).

### The summary numbers

| Number | Definition |
|---|---|
| Still to repay | The closing balance of the last installment that is due, minus prepayments dated after it up to today |
| Principal repaid | `principal + interest added − still to repay` |
| Progress | principal repaid ÷ (principal + all interest added) |
| Interest paid so far | Sum of the interest part of installments already due (cash actually paid) |
| Total interest | All installments' interest **plus** interest added to the principal |
| Total payable | `principal + total interest`, which equals the sum of every EMI |
| Projected end | Due date of the last installment; compared with `start + tenure` for "months ahead/behind" |

### The pay-off calculator

For a chosen date `T`: every installment due before `T` is assumed paid. Take the balance after the last of
them, subtract prepayments made since, and add interest for the part-month:

- If `T` **is** an installment date, that installment's full period interest is included (and so the EMI is
  *not* paid separately).
- Otherwise interest accrues **daily on actual days ÷ 365** at the rate "on file": the rate in force on `T`, or
  under `next_emi` the rate the last installment was billed at (the lender hasn't applied a newer reset yet).

An optional foreclosure charge is a percentage of the principal. It also reports the interest avoided
(everything the later installments would have charged). Dates inside an imported statement are refused,
because those rows are fixed history.

This reproduced one real lender's quote to within ₹8 on ₹15 lakh; it remains an **estimate**.

## Imports

Both JSON importers (`app/loan_import.py`, `app/data_import.py`) follow one pattern:

```
   Prompt (built from today's date and, for months, your category names)
        │   you give it to Claude with your documents; Claude asks questions, then writes a file
        ▼
   JSON file ──▶ parse(): validate, normalise, infer ──▶ review page (new vs update, warnings)
                                                              │ confirm
                                                              ▼
                                                    create(): store in your own database
```

- The **prompt** tells Claude to read first and ask one batch of questions before writing, never to guess,
  to omit personal identifiers, and to use your existing category names.
- **`parse()` is pure**: text in, a result out (`ok`, `errors`, `warnings`, `info`, normalised `data`). All the
  rules live there, so they're unit-tested without a web server. It tolerates chat noise and number formatting
  and insists on ISO dates and the `format` marker.
- **The review page re-validates** the same text on confirm instead of trusting a hidden normalised copy.
- The **Excel importer** (`app/importer.py`) predates this and reads the original workbook layout directly.

Field-level detail is in [Import formats](IMPORT_FORMATS.md).

## The front end

- **Server-rendered pages**, no framework. `app/templates/base.html` is the shell (sidebar on wide screens;
  top bar, bottom tab bar and a "More" sheet on phones).
- **`app/static/style.css`** is a small design system: colour/spacing/radius **tokens** declared once for light
  and for dark, then components (buttons, cards, stats, tables, tabs, notices, the flow bar…) built from them.
  Dark mode follows the device unless the switch has set `data-theme`, remembered in `localStorage`.
- **`app/static/app.js`** does the cross-page behaviour: the theme switch, tabs (`data-tabs`/`data-panel`,
  with the hash for deep links), copy/download buttons, drag-and-drop zones, the privacy blur, and a thin layer
  over Chart.js. Pages register their charts with `HisaabUI.mount(build)`, so when the theme changes every chart
  is rebuilt in the new colours.
- **Charts** use Chart.js, vendored in `static/` (no network). Colours come from the CSS tokens; the series
  colours are a validated colour-blind-safe set.
- **Icons and the logo** are inline SVG (`app/icons.py`): the rupee sign is drawn as strokes, so it looks the
  same on every device and is used for the sidebar, the sign-in pages and the favicon.
- **Cache-busting.** Static file URLs carry their modification time (`style.css?v=…`), so an update is picked
  up by every browser without a hard refresh.
- **Accessibility and motion.** Visible focus rings, labelled controls, text contrast checked in both themes,
  and `prefers-reduced-motion`, `prefers-reduced-transparency` and `prefers-contrast` are respected.

## Tests

Standard-library only, so there's nothing extra to install. Each suite is run with `python -m tests.<name>`.

| Suite | Guards |
|---|---|
| `test_loans` (51) | EMI formula, schedules, prepayments, rate/EMI windows, statement-based loans, interest added to the principal, the pay-off maths, the import validator, schema migrations |
| `test_data_import` (18) | The months/investments file: validation, category matching, how existing data is updated |
| `test_pages` (4) | Starts the real app and fetches **every page**, with an empty database and with data; fails on a server error, a template error, or stray text in the page head |
| `test_isolation` (6) | Two real logins against the real app: the second user must see none of the first's data on any page; plus the data-file selection and upgrade rules |
| `check_browser` | Loads every page in **headless Chrome with animations on** and fails on any console error |

Two lessons are baked into these:

- The isolation test was **verified to fail** when the original bug (everyone sharing one file) was put back, so it
  isn't a test that can only pass.
- A chart-library error once appeared only when browser animations were on, while every animation-free check
  stayed green. `check_browser` therefore leaves animations on, the way people actually use the app.

Before shipping a change, run all five. The unit suites take seconds.

## Decisions and known limits

- **SQLite, one process.** Perfect for a household; not for hundreds of concurrent users. There is no
  horizontal scaling and no need for it.
- **Euros for the monthly plan.** Salary, expenses and "invested" are in euros. Loans can be in eight
  currencies; investments are India (₹) + Germany (€). Multi-currency monthly budgets would be a larger change.
- **Monthly interest convention.** Schedules use `rate/12` per full month, matching the usual lender
  statement; partial months use days. Lenders vary in day-count and rounding, so loan figures are estimates
  that match a statement closely but not necessarily to the last unit. Importing the lender's own rows makes
  the past exact.
- **Installments before today are assumed paid on time.** There's no "I missed this one" marker.
- **One lender convention per loan** (`rate_applies`), not per rate change.
- **No email, no push notifications, no reminders.** The app is something you open, not something that nags.
- **Per-person data means no shared household view.** Two people can't see a combined picture. A shared
  household mode would need an explicit sharing model (who sees what), which is deliberately not guessed at.
- **Backups are your job**, with the script provided. There is no built-in export (the files *are* the export).

## Extending it

- **A new page:** add a route in `app/main.py` (use `d.get_db()` for data, `_render()` to render), a template
  extending `base.html`, and a nav entry in `base.html`. Add the URL to `COMMON` in `tests/test_pages.py` and
  `tests/check_browser.py`.
- **A new table for a person's data:** add its `CREATE TABLE IF NOT EXISTS` to the data tables in
  `app/database.py`. It's created in every person's file on next start. Anything that changes an *existing*
  table needs a small guarded migration like `_add_loan_columns`.
- **A new currency:** add it to `CURRENCIES` in `app/loans.py` (code → symbol).
- **A new chart:** put a `<canvas>` in the template and build it inside `HisaabUI.mount(...)` so it follows the theme.
- **Anything that reads another table's rows by id** (a new `/thing/{id}/…` route) is safe from other people's
  data by construction, but should still check the row exists and belongs to the page you think it does.
