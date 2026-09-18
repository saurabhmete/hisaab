# Hisaab — हिसाब

Self-hosted personal finance tracker: monthly salary, expenses, and dual-currency
(INR / EUR) investment tracking. FastAPI + SQLite, one container, made to run on a
Raspberry Pi behind [Tailscale](https://tailscale.com).

*Hisaab* (हिसाब) — Hindi for "accounts / the reckoning", as in *hisaab rakhna*, to
keep one's accounts.

## Features

- **Monthly view** — salary, per-category expenses, what's left after expenses and
  after investing. Data is entered directly on the site.
- **Year grid** — the classic spreadsheet layout: categories × months.
- **Investments** — India (₹) and Germany (€) values and profits per month; net
  worth in INR, profit percentages, and the EUR→INR conversion are computed for you.
- **Dashboard** — salary vs expenses vs invested per month, and a net-worth trend.
- **Excel import** — upload the original "Salary Plan" workbook on the *Import*
  page (or `python import_excel.py file.xlsx`) to seed the database. After that
  the site is the source of truth; import stays available as an option.
- **Users & login** — on first launch the app asks you to create an **admin**
  account. The setup page is protected by a one-time token printed in the server
  log (`docker compose logs hisaab`) — open the `/setup?token=…` link it shows,
  so a publicly exposed fresh install can't be claimed by a stranger. (Set
  `HISAAB_SETUP_TOKEN` to choose the token yourself; a random one is generated
  per start otherwise.) Admins can add and remove users on the *Users* page (e.g. add your
  partner as a regular member); members can't manage users, and the last admin
  can never be deleted. Everyone shares the same household data.
  Prefer no login wall at all (Tailscale already limits who can reach the Pi)?
  Set `HISAAB_AUTH=off` in the environment.
- **No cloud** — a single SQLite file (`data/hisaab.db`) on your own
  hardware. Chart.js is vendored, so it works fully offline.

## Run locally

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

## Deploy on the Raspberry Pi (Docker)

```sh
git clone <your-repo> hisaab && cd hisaab
docker compose up -d --build
```

The app listens on port 8000 and stores its database in `./data/` on the host.
On a tailnet, open `http://<pi-tailscale-name>:8000` from any of your devices.

### HTTPS URL with Tailscale Serve or Funnel

Tailnet-only (recommended — reachable from your own devices anywhere, invisible
to the internet). If port 443 is already taken by another app on the Pi, use one
of the alternative ports (8443 or 10000):

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8000
# → https://<pi-name>.<tailnet>.ts.net:8443
```

Public internet via Funnel (no VPN client needed on the visiting device):

```sh
tailscale funnel --bg --https=8443 http://127.0.0.1:8000
```

With Funnel the login page is exposed to the whole internet, so the app ships
with brute-force protection: 5 failed logins from an IP within 15 minutes lock
that IP out for the rest of the window, session cookies are `Secure` +
`HttpOnly`, and passwords are stored as salted PBKDF2 hashes. Use strong
passwords, and never set `HISAAB_AUTH=off` on a funneled deployment.

## Backups

The whole state is one file. A nightly cron on the Pi is enough:

```sh
# crontab -e
0 3 * * * sqlite3 /path/to/hisaab/data/hisaab.db ".backup /path/to/backups/hisaab-$(date +\%a).db"
```

## Privacy

`.gitignore` excludes `data/` and all `.xlsx`/`.db` files — the code can live on
GitHub, the numbers never leave your Pi.
