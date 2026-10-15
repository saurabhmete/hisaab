# Deployment guide

Running Hisaab on a Raspberry Pi (or any Linux box) with Docker, reachable from your phone anywhere
through Tailscale, with nightly backups. Everything here has been run for real; the commands are copy-paste.

**Contents**
[What you need](#what-you-need) ·
[Install Docker](#1-install-docker) ·
[Get the code and start it](#2-get-the-code-and-start-it) ·
[Create the first account](#3-create-the-first-account) ·
[Reach it from anywhere](#4-reach-it-from-anywhere-tailscale) ·
[Updating](#updating) ·
[Backups](#backups) ·
[Restoring](#restoring-from-a-backup) ·
[Settings](#settings) ·
[Troubleshooting](#troubleshooting)

---

## What you need

- A Raspberry Pi (any model that runs 64-bit Linux and Docker; a Pi 4 is plenty) or another Linux machine
- A [Tailscale](https://tailscale.com) account with Tailscale installed on the Pi and on your phone/laptop
- SSH access to the Pi

Hisaab is one small container and one SQLite file per person. It idles at almost no CPU or memory.

## 1. Install Docker

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in (so the group change applies), then check:

```sh
docker --version && docker compose version
```

On a very new Ubuntu release the official repository may not have packages yet. Use Ubuntu's own:
`sudo apt install -y docker.io docker-compose-v2`.

## 2. Get the code and start it

```sh
git clone https://github.com/saurabhmete/hisaab.git
cd hisaab
```

**If something else on the Pi already uses port 8000**, give Hisaab a different one. Find out with
`sudo ss -tlnp | grep :8000`, then:

```sh
echo "HISAAB_PORT=8100" > .env
```

Start it:

```sh
docker compose up -d --build
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8100/login     # expect 200 (use your port)
```

The first build takes a few minutes on a Pi. The container restarts by itself after a crash or reboot
(`restart: unless-stopped`). Your data lives in `./data` next to the compose file, outside the container,
so rebuilding never touches it.

## 3. Create the first account

On a fresh install, Hisaab won't let anyone create the admin account without a private link, so that a
stranger who finds the address can't claim it first. The link is in the container's log:

```sh
docker compose logs hisaab | grep -A 3 "first-run setup"
```

Take the `/setup?token=…` part, put your server's address in front of it, open it and create the admin
account. The token changes every time the container starts (set `HISAAB_SETUP_TOKEN` to choose your own).
Once an account exists, `/setup` just redirects to the login page.

## 4. Reach it from anywhere (Tailscale)

Tailscale gives the Pi a stable name like `pi.your-tailnet.ts.net` and a real HTTPS certificate. There
are two modes; pick one.

| | `tailscale serve` | `tailscale funnel` |
|---|---|---|
| Who can reach it | Only your own devices, logged into your tailnet | **Anyone on the internet** who has the link |
| Needs the Tailscale app on the device | Yes | No |
| Use when | You (and family) can install the app | You want to open it on any browser |

```sh
# only your tailnet:
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8100

# or the public internet:
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8100

tailscale serve status            # or: tailscale funnel status
```

Your address is then `https://<pi-name>.<tailnet>.ts.net:8443`. The `--bg` makes it permanent: it survives
reboots. Funnel can only use the HTTPS ports **443, 8443 and 10000** (stick to those for `serve` too, to keep
things simple); if another app already uses 443 on the same Pi, use 8443 as above. Turn it off with
`sudo tailscale funnel --https=8443 off` (or `serve`).

### If you use Funnel, read this

Funnel puts your sign-in page on the public internet. Hisaab is built for that (passwords are hashed,
sessions are `Secure` + `HttpOnly`, five wrong passwords lock an address out for 15 minutes, the setup
page needs a private token) but the passwords are your last line of defence:

- Use **long, unique passwords** for every account.
- **Never** set `HISAAB_AUTH=off` on a funnelled install: it removes the login entirely.
- Keep the Pi and Docker updated.
- If you only need access from your own devices, prefer `serve`: nothing is exposed at all.

## Updating

```sh
cd ~/hisaab && git pull && docker compose up -d --build
```

That's the whole update. It pulls the latest code and rebuilds and restarts the container in place; your
data in `./data` is untouched. Database changes (new tables, new columns, the move to per-person data
files) are applied **automatically** the first time the new version starts.

- **Hard-refresh** the browser once afterwards (Cmd+Shift+R). The app tags its scripts and styles with a
  version, so this is rarely needed, but it costs nothing.
- **Before a big update**, take a backup first (see below).
- **To go back** to the previous version: `git log --oneline` to find it, `git checkout <commit>`,
  `docker compose up -d --build`. Go forward again with `git checkout main && git pull`.
- If `git pull` complains about local changes, don't force it. Look at `git status`; an old hand-edit of
  `docker-compose.yml` is the usual cause (the port now comes from `.env`, so
  `git checkout docker-compose.yml` is safe).

## Backups

What needs backing up:

| File | Contains |
|---|---|
| `data/hisaab.db` | The accounts, **and** the first account's data |
| `data/users/<id>.db` | One file per additional account |

[`scripts/backup.sh`](../scripts/backup.sh) copies all of them with SQLite's own `.backup` command (safe
while the app is running), keeps **one copy per weekday** (7 days of history), and writes a line per
database to `backup.log` so you can see it ran. The extra accounts' files are created by the container and
belong to root, so schedule it in **root's** crontab:

```sh
sudo crontab -e
```

Add two lines (use your own username and the real path to the script):

```
BACKUP_OWNER=yourname
0 3 * * * /home/yourname/hisaab/scripts/backup.sh
```

`BACKUP_OWNER` tells the script whose `~/hisaab/data` and `~/backups` to use and who should own the
copies. Check it:

```sh
sudo crontab -l                                   # shows the two lines
sudo /home/yourname/hisaab/scripts/backup.sh      # run it once by hand
tail -3 ~/backups/backup.log                      # "… backed up hisaab"
ls -la ~/backups                                  # hisaab-Thu.db, 2-Thu.db, …
```

Tomorrow morning `~/backups/backup.log` should have a new line with that day's date. If it doesn't,
cron didn't run it: check `sudo crontab -l` and that the Pi was on at 3 AM.

**Keep a copy off the Pi.** An SD card can die. Occasionally copy `~/backups` to another machine, for
example from your computer: `rsync -av pi:~/backups/ ~/hisaab-backups/`.

**Check a backup is sound:** `sqlite3 ~/backups/hisaab-Mon.db "pragma integrity_check"` should print `ok`.

> A backup of only `hisaab.db` (the old advice, from before accounts were separated) misses every
> additional account. Use the script.

## Restoring from a backup

Stop the app first so nothing writes while you copy:

```sh
cd ~/hisaab
docker compose stop
```

Copy back only what you need. Replace `Mon` with the weekday of the backup you want:

```sh
# accounts + the first account's data:
sudo cp ~/backups/hisaab-Mon.db data/hisaab.db

# one additional account (here: account 2):
sudo cp ~/backups/2-Mon.db data/users/2.db
sudo chmod 600 data/users/2.db

docker compose start
```

Restoring `hisaab.db` also restores the *list of accounts and sessions* as they were that day, so anyone
added since then needs adding again.

## Settings

| Setting | Where | What it does |
|---|---|---|
| `HISAAB_PORT` | `.env` next to `docker-compose.yml` | The port on the Pi that the app listens on (default 8000) |
| `HISAAB_SETUP_TOKEN` | environment | Fixes the first-run setup token instead of a random one per start |
| `HISAAB_AUTH` | environment | `off` removes the login (everyone is an admin on one shared data file). Only for a private machine; never with Funnel |
| `HISAAB_DB` | environment | Path of the main database (the Docker image sets `/hisaab/data/hisaab.db`) |

To pass one to the container add it under `environment:` in `docker-compose.yml`.

## Running without Docker

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
```

Data goes to `data/` next to the code unless `HISAAB_DB` says otherwise. You'll need to keep it running
yourself (a systemd service, for example); the Docker setup does that for you.

## Troubleshooting

**The container won't start, or the page doesn't load.**
`docker compose ps` then `docker compose logs --tail 30 hisaab`. A Python traceback at the end is the cause.

**"address already in use" on start.**
Another program has the port. `sudo ss -tlnp | grep :8000` shows who; either stop it or set a different
`HISAAB_PORT` in `.env` and run `docker compose up -d` again. Remember to point Tailscale at the new port.

**`git pull` says "Could not resolve host: github.com".**
The Pi can't look up names; it isn't Hisaab. Work through:

```sh
ping -c 2 1.1.1.1        # fails → no internet at all (cable, Wi-Fi, router)
ping -c 2 github.com     # fails while the first works → DNS
resolvectl status | head -20
sudo systemctl restart systemd-resolved
getent hosts github.com  # should now print an address
```

If it's still broken, set a public DNS temporarily for your network interface (the name is in the
`resolvectl status` output):

```sh
sudo resolvectl dns eth0 1.1.1.1 8.8.8.8
```

**You need to update right now and DNS won't cooperate.** Send the update from your computer instead:

```sh
# on your computer
cd hisaab && git bundle create /tmp/hisaab.bundle main && scp /tmp/hisaab.bundle pi:/tmp/
# on the Pi
cd ~/hisaab && git pull /tmp/hisaab.bundle main && docker compose up -d --build
```

The rebuild reuses cached layers, so it works offline as long as `requirements.txt` hasn't changed.

**The site works on the Pi but not through Tailscale.**
`tailscale serve status` / `tailscale funnel status` should list your address pointing at the right local
port. Check the port matches `HISAAB_PORT` and that `curl http://127.0.0.1:<port>/login` prints 200 on the Pi.

**Charts or styles look wrong after an update.**
Hard-refresh the browser (Cmd+Shift+R). If it persists, open the browser console (F12): a red error names
the file that failed to load.

**After a reboot the site is gone.**
It shouldn't be: Docker restarts the container and Tailscale restores `--bg` routes. Check
`docker compose ps` and `tailscale serve status` (or `funnel status`).

**Login is locked ("Too many failed attempts").**
Five wrong passwords lock that address for 15 minutes; just wait. To clear it immediately, on the Pi:
`sudo sqlite3 ~/hisaab/data/hisaab.db "delete from login_failures"`.

**I forgot a password.**
See the [user guide](USER_GUIDE.md#tips-and-troubleshooting) for the one-line reset.
