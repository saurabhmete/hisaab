#!/bin/sh
# Back up every Hisaab database and keep one copy per weekday (so 7 days of history).
#
#   hisaab.db            the accounts, plus the first account's data
#   users/<id>.db        one file per additional account
#
# Reads the live files with SQLite's own `.backup`, which is safe while the app is running.
# Writes a line per database to <backups>/backup.log, so you can see that it ran.
#
# The extra accounts' files belong to root (the container writes them), so run this as root
# (sudo, or root's crontab). Where to look is taken from the user who owns the install:
#   BACKUP_OWNER    login name of that user       (default: the user running sudo, else you)
#   HISAAB_DATA     folder holding hisaab.db      (default: ~owner/hisaab/data)
#   HISAAB_BACKUPS  where copies are written      (default: ~owner/backups)

OWNER="${BACKUP_OWNER:-${SUDO_USER:-$(id -un)}}"
if [ -z "$HISAAB_DATA" ] || [ -z "$HISAAB_BACKUPS" ]; then
  HOME_DIR="$(getent passwd "$OWNER" 2>/dev/null | cut -d: -f6)"
  if [ -z "$HOME_DIR" ]; then
    echo "Cannot find the home folder of '$OWNER'. Set HISAAB_DATA and HISAAB_BACKUPS." >&2
    exit 1
  fi
fi
SRC="${HISAAB_DATA:-$HOME_DIR/hisaab/data}"
DEST="${HISAAB_BACKUPS:-$HOME_DIR/backups}"

mkdir -p "$DEST"
exec >> "$DEST/backup.log" 2>&1

for f in "$SRC"/hisaab.db "$SRC"/users/*.db; do
  [ -f "$f" ] || continue
  name=$(basename "$f" .db)
  if sqlite3 "$f" ".backup $DEST/$name-$(date +%a).db"; then
    echo "$(date '+%F %T') backed up $name"
  else
    echo "$(date '+%F %T') FAILED $name"
  fi
done

chown -R "$OWNER": "$DEST"
