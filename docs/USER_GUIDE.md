# User guide

How to use Hisaab, page by page. For setting it up on a Raspberry Pi see the
[deployment guide](DEPLOYMENT.md); for how it works inside see [architecture](ARCHITECTURE.md).

**Contents**
[First run](#first-run-and-signing-in) ·
[Finding your way around](#finding-your-way-around) ·
[Planning a month](#planning-a-month) ·
[Overview](#the-overview) ·
[History](#history) ·
[Investments](#investments) ·
[Loans](#loans) ·
[Importing your data](#importing-your-data) ·
[People and privacy](#people-and-privacy) ·
[Tips and troubleshooting](#tips-and-troubleshooting)

---

## First run and signing in

The very first time, Hisaab has no accounts. To stop a stranger from claiming the admin account on a
publicly reachable install, the setup page is locked until you open a **private link** that the
server prints when it starts:

```sh
docker compose logs hisaab        # on the Pi
# or look at the terminal where you ran uvicorn
```

Look for a box that says *"Hisaab first-run setup"* and shows `/setup?token=…`. Add your server's
address in front of it, open it, and create the admin account (username, and a password of at least
8 characters). From then on you sign in with that username and password on the normal login page.

Sessions last 90 days on each device. **Log out** is at the bottom of the sidebar (on a phone: *More*).

## Finding your way around

**On a computer**, the sidebar on the left has:

| Item | What it is |
|---|---|
| **Overview** | This month at a glance (and where you go to plan the next one) |
| **History** | Every year, with a spreadsheet-style grid |
| **Investments** | What you hold in India and Germany, and how it's growing |
| **Loans** | EMIs and loans |
| **Import** | Bring in data you already have |
| **Users** | Add or remove people (admins only) |

At the bottom: your name, a **light/dark switch** (sun/moon), and **log out**.

**On a phone**, the same pages are in a tab bar at the bottom (Overview, History, Investments, Loans);
**More** opens Import, Users and log out. The light/dark switch is in the top bar.

Light or dark follows your device by default; the switch overrides it and is remembered on that device.

## Planning a month

This is the heart of the app. You do it once at the start of each month. From the Overview press
**Add <next month>** (or use the arrows on the planner page to move between months).

The page has three steps, and a summary that stays in view on the right as you type (on a phone it
becomes a floating bar at the bottom):

1. **Salary.** What you'll receive this month.
2. **Plan the expenses.** Your regular categories (Rent, Groceries, Gym…) are **pre-filled from your last
   recorded month**, because most of them don't change. Edit an amount, press **✕** to remove a row,
   or **Add expense** for something new. Category names autocomplete from ones you've used before.
   If a loan has an EMI due this month, a note offers a one-click **Add EMI: <loan>** button
   (euro loans only, since the planner works in euros).
3. **Where the money goes.** The panel shows, line by line:

   ```
   Salary                            €3,400
   Planned expenses                 −€1,757
   Left after expenses               €1,643     ← the most you can invest
   Invest this month   [ 900 ]  [₹1 lakh] [All of it]
   Remainder in account                €743
   ```

   Type how much you'll invest, or use a shortcut: **₹1 lakh** fills in what ₹1,00,000 is in euros at
   the latest rate you've saved, and **All of it** fills in everything that's left. The **Remainder** turns
   red if you've committed more than you have.

Nothing is saved until you press **Save month**. A pre-filled new month is only a suggestion until then.
Saving replaces that month's category list with what you see, so a row you removed is gone.

## The Overview

The first screen after signing in, for the latest month (use the arrows or click the month's name to jump).

- **The big number** is what's left after expenses *and* investing, with a plain-English sentence under
  it. Badges show the share of salary you kept and whether you spent more or less than the month before.
- **The bar** splits the salary into expenses (orange), invested (green) and left over (blue). If you
  spent more than you earned, it turns red and the headline says *Over budget*.
- **Where it went** lists the categories as bars, biggest first.
- **The year chart** shows salary, expenses and investing for every month so far.
- **Investments** and **Loans** cards summarise your net worth and what you still owe, and link through.

**Edit month** reopens the planner for the month shown.

## History

**History** lists every year as a card with the totals for salary, expenses, invested and what was left.
Open a year for the **grid**: one row per category and one column per month, with the **year total right
next to the labels** so you don't have to scroll sideways. Click a month's name to edit it.

## Investments

Investments are recorded as a **monthly snapshot**: what your holdings are worth at the end of the month.

**Add a month** (top right) opens *Add or update a month*:

| Field | Meaning |
|---|---|
| India value / profit | Your holdings in India and your total profit on them, in rupees |
| Germany value / profit | Your holdings in Germany and your total profit on them, in euros |
| EUR → INR rate | Pre-filled with today's ECB rate; change it if you use your own transfer rate |

Saving a month that already exists replaces it. Everything else is worked out for you: net worth in rupees,
each country's profit %, and the overall profit %.

- **The headline numbers** value your latest holdings at **today's** EUR→INR rate (fetched from the ECB,
  cached for six hours, and silently skipped if you're offline: it then uses the rate you saved).
- **The table and charts** use the rate saved with *each* month, so the past isn't rewritten whenever the
  euro moves.
- **The eye icon** (top right) blurs every amount on this page, for when someone is looking over your
  shoulder or you're giving a demo. It's remembered per device. It hides numbers on screen only; it isn't
  a security feature.

## Loans

### Adding a loan you've just taken

**Loans → Add loan.** Fill in the loan as it was first sanctioned:

- **Name**, optional **lender**, **currency** (EUR, INR, USD, GBP, CHF, AED, CAD or AUD)
- **Loan amount** and **start date** (the first EMI falls one month after it)
- **Interest rate** (per year) and **tenure** (months). Leave **EMI** blank and it's calculated for you;
  the panel on the right shows it as you type, and how much of the total is interest.

### The loan page

Each loan has five tabs.

**Overview.** *Still to repay*, *principal repaid*, *interest paid so far*, the current EMI and rate, how
many EMIs remain, and a progress bar. Under it: total interest over the whole loan, total payable, and
the **projected end date**, which says how many months ahead of or behind the original schedule you are.
Two charts: what you still owe over time (solid = already due, dashed = projection) and principal vs
interest per year.

**EMI & rate history.** Real loans change. Record each change with the date it took effect:

- *Did your EMI change?* Enter **From** and the new EMI. Leave **Until** empty if it continues, or fill it
  in for a **temporary** EMI: the previous amount resumes the day after.
- *Did the rate change?* Same idea. For a **floating** rate that resets for a period, give **From** and
  **Until**; the earlier rate comes back afterwards by itself.
- **Prepayments** are extra payments toward the principal. Interest is recalculated from the day you paid.

Each table shows when every value applied (*From → To*), highlights the one in force today, and has a ✕
to remove an entry.

If your lender raises the rate but keeps your EMI, the loan simply gets longer; if it changes the EMI,
record that too.

**Schedule.** Every installment: due date, EMI, principal, interest, balance. Paid rows are dimmed, the
next one is highlighted. Rows from an imported statement are marked *statement*.

**Pay off early.** See [below](#paying-a-loan-off-early).

**Settings.** Edit the loan's details, choose **when a rate change takes effect** (see below), or delete the
loan (with its whole history).

### Paying a loan off early

*Loan → Pay off early.* Pick the date you'd pay everything and, if your lender charges one, a
**foreclosure charge** (% of the outstanding principal). Hisaab shows what clears the loan that day:

```
Outstanding principal     ₹1,501,183
Interest for 20 days      ₹   12,009     ← daily, on a 365-day year
Foreclosure charge (2%)   ₹   30,024
Total to pay              ₹1,543,216
```

It also tells you how many EMIs will have been paid by then and **how much future interest you'd avoid**.

Things worth knowing:

- It assumes every EMI until that date is paid on time.
- If the date is an EMI date, that EMI is **part of** the payoff; don't pay it separately.
- It's an **estimate**. Lenders count days, charges and taxes their own way, so ask for the official
  foreclosure letter before transferring money. If your bank's figure differs, the most likely cause is
  the next setting.

### "When a rate change takes effect"

Some lenders apply a new rate to the EMI due *on the day it takes effect*; others only from the *next* EMI.
Under **Settings** you choose which matches your lender. Look at the interest figures on your lender's own
statement just after a past rate change to tell. This setting decides which EMI first uses the new rate and
which rate a pay-off quote between EMIs uses.

### A loan you've been paying for years

Re-entering years of history by hand would be tedious and error-prone, and some loans (interest-only
periods, unpaid interest added to the principal) can't be rebuilt from the original terms at all. Instead,
**Loans → Import a running loan**: you give Claude your lender's documents and a prompt, it makes a file,
and Hisaab keeps the lender's **real past installments exactly as recorded** and projects the rest from
the last one. See [Importing your data](#importing-a-running-loan).

## Importing your data

Open **Import**. Importing is always optional; everything can be entered by hand.

### The Excel sheet ("Salary Plan")

If your records are in the original layout, drop the `.xlsx` onto the **I have the Salary Plan
spreadsheet** tab. Months that already exist are overwritten with the sheet's values. The expected layout
is described on the page and in [Import formats](IMPORT_FORMATS.md#the-excel-workbook). There's also a
command-line version: `python import_excel.py "Salary Plan.xlsx"`.

### Anything else, with Claude

Records in bank statements, a different spreadsheet, another app? Use the **My records are in some other
form** tab:

1. **Copy the prompt** (it already lists your current expense categories so Claude reuses them).
2. **Ask Claude**: open a chat at claude.ai (or the app), paste the prompt, and attach your records.
   Claude will **ask you questions first**: what counts as salary, which payments are investments,
   which transfers to ignore. Answer them; that's what makes the result accurate.
3. **Upload the file** Claude gives you (or paste the JSON). 

Hisaab then shows a **review page** before anything is saved: how many months are new or updated, the
amounts that would be replaced (*was €500, now €700*), and any warnings. Press **Import this** to
confirm. Existing months are *updated*, never emptied: categories the file doesn't mention stay as they are.

Your documents contain personal details; the prompt tells Claude to leave names, addresses and account
numbers out of the file.

### Importing a running loan

**Loans → Import a running loan.** Same three steps: copy the prompt, give it to Claude with your lender's
**repayment schedule or statement** and your **interest-rate change table**, upload the file. Claude asks
about anything unclear (the date format, whether there was an interest-only period, any extra payments).

The review page shows what you still owe today, how many installments were recorded, interest paid, and
any interest that was added to the principal, plus what Hisaab will project and when the loan ends.
If you know your bank's statement balance, check it against the one shown before confirming.

## People and privacy

**Users** (admins only) lists everyone with access.

- **Add a user** with a username and password; tick *Make admin* only for people who should manage accounts.
- **Each person has their own private data.** A new account starts empty. People can't see each other's
  months, investments or loans, and **admins can't either**: admin only means "can add and remove people".
- **Removing a person permanently deletes their data**, and signs them out at once. You can't remove
  yourself, the last admin, or the first account (it holds the original data).

Everyone uses the same address; each signs in with their own username.

## Tips and troubleshooting

- **Numbers or charts look stale after an update:** hard-refresh once (Cmd+Shift+R). The app tags its
  files so this should rarely be needed.
- **A page says there's nothing for a month:** that month hasn't been planned yet. Press *Plan* on it.
- **My loan's remaining balance doesn't match my bank:** check *Settings → when a rate change takes
  effect*, and the date of the newest installment on your statement. Anything after that is projected.
  Importing a newer statement makes it exact again.
- **The pay-off amount differs from my bank's quote:** see [Paying a loan off early](#paying-a-loan-off-early).
  Day counts, rounding and charges differ between lenders; small differences are normal.
- **I forgot my password:** there is no email reset, because the app never sends email. Whoever runs the
  server can set a new password from a terminal on the Pi (replace `yourname` and the new password):

  ```sh
  cd ~/hisaab
  docker compose exec hisaab python -c "from app import database as d; db = d._connect(d.DB_PATH); db.execute('UPDATE users SET password_hash=? WHERE username=?', (d.hash_password('A-NEW-PASSWORD'), 'yourname')); db.commit()"
  ```

  Your data is untouched. (If the command is split when you paste it, type it as one line.)
- **Locked out after typing the wrong password:** five wrong attempts from one address lock that address
  out for 15 minutes.
