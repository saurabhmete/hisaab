#!/usr/bin/env python3
"""CLI import: python import_excel.py "path/to/Salary Plan.xlsx" [username]

Same importer as the web UI's /import page. Without a username it imports into
the first account's data (the one that existed before accounts were separated);
give a username to import into that person's data instead.
"""
import sys

from app import database as d
from app import importer


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    d.init_db()
    path = d.DB_PATH
    if len(sys.argv) == 3:
        with d.get_auth_db() as auth:
            row = auth.execute("SELECT id FROM users WHERE username=?", (sys.argv[2],)).fetchone()
            if row is None:
                sys.exit(f"No user called {sys.argv[2]!r}.")
            path = d.data_path_for(auth, row["id"])
    with d.get_db(path) as db:
        report = importer.import_workbook(db, sys.argv[1])
    for sheet, count in report.items():
        print(f"{sheet}: {count} entries")


if __name__ == "__main__":
    main()
