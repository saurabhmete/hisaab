# Changelog

What changed in Hisaab, newest first. Dates are when each change landed. Upgrades are automatic: the
database is brought up to date the first time a new version starts, and your data is never lost.
To update, see [deployment](docs/DEPLOYMENT.md#updating).

## 15 October 2026

### Documentation
- New guides: [user guide](docs/USER_GUIDE.md), [deployment](docs/DEPLOYMENT.md),
  [import formats](docs/IMPORT_FORMATS.md) and [architecture](docs/ARCHITECTURE.md), and a rewritten README.
- Screenshots retaken on the new design, in light and dark and on a phone (demo data only).
- `scripts/backup.sh`: backs up every database, one copy per weekday, with a log you can check.

### Privacy: every person now has their own data
**Important fix.** Until now every account shared one set of data, so a newly added user could see the
first user's months, investments and loans. Now each account has its own database file, chosen from who is
logged in; a new account starts empty, admins can't see anyone else's money, and removing a person deletes
their data. The first account keeps the original data. An automated test logs in as two people and checks
this, and was verified to fail when the old behaviour is put back.
**After updating, change your backups** to cover the extra files: see [Backups](docs/DEPLOYMENT.md#backups).

### A new look
- A calm, consistent design in **light and dark** (with a switch), a sidebar on computers and a tab bar on
  phones, and a new **₹ logo**.
- **Overview** leads with *how much is left this month*, a bar of where the salary went, category bars,
  the year chart, and net worth and loans at a glance.
- **Month planner** keeps a live summary beside the form.
- The **loan page** is split into tabs: Overview, Pay off early, EMI & rate history, Schedule, Settings.
- Import pages have drag-and-drop file zones and numbered steps.
- Charts fixed: line charts failed to draw in real browsers (where animations are on). A new check loads
  every page in headless Chrome with animations on and fails on any console error.
- Static files carry a version, so browsers never keep an old copy after an update.

### Loans
- **Pay off early:** pick a date and see exactly what clears the loan, including interest for the part-month
  (daily, actual/365), an optional foreclosure charge, and the future interest you'd avoid.
- **When a rate change applies** is now a per-loan setting (from the EMI date, or from the next EMI), so a
  pay-off quote can follow your lender's own habit. A real quote that was ₹291 off is now within ₹8.
- EMI and rate changes can have an **until** date (floating-rate periods, temporary EMIs); the earlier value
  resumes automatically.
- Eight currencies: EUR, INR, USD, GBP, CHF, AED, CAD, AUD.
- **Import a running loan** from your lender's schedule with a prompt for Claude. The lender's real past
  installments are kept as recorded, including interest-only periods and unpaid interest that was later added
  to the principal.
- **Loans and EMIs:** loan amount, start date, tenure, separate EMI and rate histories with dates,
  prepayments, the full amortisation schedule, charts, and one-click EMIs in the month planner.

### Import
- **Import anything else with Claude:** months, expenses, investments from statements, other spreadsheets or
  app exports. Claude is told to ask about anything unclear first. A review page shows what's new, what would
  be replaced, and warnings before anything is saved. Your existing category names are matched, ignoring case.

## 18 September 2026

### First release
- **Month planner** with last month's categories pre-filled, a live "what can I invest" ledger, and
  quick-fill buttons.
- **Overview** with a month switcher, **History** (year cards and a categories × months grid), and
  **Investments** for India (₹) and Germany (€) with net worth and profit.
- **Live EUR→INR rate** from the ECB (optional; falls back to your saved rate), and the headline net worth
  always at today's rate.
- **Privacy eye** on the Investments page to blur amounts for a demo.
- **Excel import** of the original "Salary Plan" workbook.
- **Users and login:** PBKDF2 password hashing, sessions, sign-in lockout after repeated failures, an admin
  who manages users, and a first-run setup page protected by a private token.
- **Deployment:** Docker image and compose file for a Raspberry Pi, behind Tailscale Serve or Funnel, with a
  configurable port.
- **Phone-friendly** layout from the start.
