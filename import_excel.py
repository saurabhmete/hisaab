#!/usr/bin/env python3
"""CLI import: python import_excel.py "path/to/Salary Plan.xlsx"

Same importer as the web UI's /import page — use whichever is handier.
"""
import sys

from app import database as d
from app import importer


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    d.init_db()
    with d.get_db() as db:
        report = importer.import_workbook(db, sys.argv[1])
    for sheet, count in report.items():
        print(f"{sheet}: {count} entries")


if __name__ == "__main__":
    main()
