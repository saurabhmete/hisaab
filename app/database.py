"""SQLite layer for Hisaab. One file DB, stdlib sqlite3, no ORM."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "HISAAB_DB", os.path.join(os.path.dirname(__file__), "..", "data", "hisaab.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS months (
    id       INTEGER PRIMARY KEY,
    year     INTEGER NOT NULL,
    month    INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    salary   REAL NOT NULL DEFAULT 0,
    invested REAL NOT NULL DEFAULT 0,
    UNIQUE (year, month)
);

CREATE TABLE IF NOT EXISTS expenses (
    id       INTEGER PRIMARY KEY,
    month_id INTEGER NOT NULL REFERENCES months(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    amount   REAL NOT NULL DEFAULT 0,
    UNIQUE (month_id, category)
);

CREATE TABLE IF NOT EXISTS investments (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL,
    month          INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    india_value    REAL,
    india_profit   REAL,
    germany_value  REAL,
    germany_profit REAL,
    eur_inr_rate   REAL NOT NULL DEFAULT 110.0,
    UNIQUE (year, month)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY,
    ip TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)


@contextmanager
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ---------- users / auth ----------

import hashlib
import secrets

_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$", 1)
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS
    ).hex()
    return secrets.compare_digest(candidate, digest)


def create_user(db, username: str, password: str, is_admin: bool) -> int:
    cur = db.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
        (username.strip(), hash_password(password), 1 if is_admin else 0),
    )
    return cur.lastrowid


def authenticate(db, username: str, password: str):
    row = db.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if row and verify_password(password, row["password_hash"]):
        return row
    return None


def user_count(db) -> int:
    return db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def admin_count(db) -> int:
    return db.execute("SELECT COUNT(*) AS c FROM users WHERE is_admin=1").fetchone()["c"]


def all_users(db):
    return db.execute("SELECT * FROM users ORDER BY created_at").fetchall()


def delete_user(db, user_id: int):
    db.execute("DELETE FROM users WHERE id=?", (user_id,))


def create_session(db, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def session_user(db, token: str):
    if not token:
        return None
    return db.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token=? AND s.created_at > datetime('now', '-90 days')""",
        (token,),
    ).fetchone()


def delete_session(db, token: str):
    db.execute("DELETE FROM sessions WHERE token=?", (token,))


# Brute-force protection for the public (Funnel) deployment: an IP that fails
# LOGIN_MAX_FAILURES times within LOGIN_WINDOW_MINUTES is locked out for the
# rest of the window.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_MINUTES = 15


def record_login_failure(db, ip: str):
    db.execute("INSERT INTO login_failures (ip) VALUES (?)", (ip,))
    db.execute("DELETE FROM login_failures WHERE at < datetime('now', '-1 day')")


def recent_login_failures(db, ip: str) -> int:
    return db.execute(
        "SELECT COUNT(*) AS c FROM login_failures WHERE ip=? AND at > datetime('now', ?)",
        (ip, f"-{LOGIN_WINDOW_MINUTES} minutes"),
    ).fetchone()["c"]


def clear_login_failures(db, ip: str):
    db.execute("DELETE FROM login_failures WHERE ip=?", (ip,))


# ---------- months / expenses ----------

def upsert_month(db, year: int, month: int, salary=None, invested=None) -> int:
    db.execute(
        "INSERT INTO months (year, month) VALUES (?, ?) ON CONFLICT(year, month) DO NOTHING",
        (year, month),
    )
    if salary is not None:
        db.execute("UPDATE months SET salary=? WHERE year=? AND month=?", (salary, year, month))
    if invested is not None:
        db.execute("UPDATE months SET invested=? WHERE year=? AND month=?", (invested, year, month))
    row = db.execute("SELECT id FROM months WHERE year=? AND month=?", (year, month)).fetchone()
    return row["id"]


def get_month(db, year: int, month: int):
    return db.execute(
        "SELECT * FROM months WHERE year=? AND month=?", (year, month)
    ).fetchone()


def set_expense(db, month_id: int, category: str, amount: float):
    db.execute(
        """INSERT INTO expenses (month_id, category, amount) VALUES (?, ?, ?)
           ON CONFLICT(month_id, category) DO UPDATE SET amount=excluded.amount""",
        (month_id, category.strip(), amount),
    )


def delete_expense(db, expense_id: int):
    db.execute("DELETE FROM expenses WHERE id=?", (expense_id,))


def month_expenses(db, month_id: int):
    return db.execute(
        "SELECT * FROM expenses WHERE month_id=? ORDER BY amount DESC, category", (month_id,)
    ).fetchall()


def month_summary(db, row) -> dict:
    """Derived numbers for a months row."""
    total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM expenses WHERE month_id=?", (row["id"],)
    ).fetchone()["t"]
    return {
        "total_expense": total,
        "after_expense": row["salary"] - total,
        "after_invest": row["salary"] - total - row["invested"],
    }


def year_months(db, year: int):
    return db.execute(
        "SELECT * FROM months WHERE year=? ORDER BY month", (year,)
    ).fetchall()


def all_years(db):
    rows = db.execute(
        "SELECT DISTINCT year FROM months UNION SELECT DISTINCT year FROM investments ORDER BY 1"
    ).fetchall()
    return [r["year"] for r in rows]


def previous_month_row(db, year: int, month: int):
    """The most recent recorded month strictly before (year, month)."""
    return db.execute(
        """SELECT * FROM months WHERE year < ? OR (year = ? AND month < ?)
           ORDER BY year DESC, month DESC LIMIT 1""",
        (year, year, month),
    ).fetchone()


def latest_month(db):
    return db.execute(
        "SELECT * FROM months ORDER BY year DESC, month DESC LIMIT 1"
    ).fetchone()


# ---------- investments ----------

def upsert_investment(db, year, month, india_value, india_profit,
                      germany_value, germany_profit, eur_inr_rate):
    db.execute(
        """INSERT INTO investments
             (year, month, india_value, india_profit, germany_value, germany_profit, eur_inr_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(year, month) DO UPDATE SET
             india_value=excluded.india_value, india_profit=excluded.india_profit,
             germany_value=excluded.germany_value, germany_profit=excluded.germany_profit,
             eur_inr_rate=excluded.eur_inr_rate""",
        (year, month, india_value, india_profit, germany_value, germany_profit, eur_inr_rate),
    )


def delete_investment(db, inv_id: int):
    db.execute("DELETE FROM investments WHERE id=?", (inv_id,))


def all_investments(db):
    return db.execute("SELECT * FROM investments ORDER BY year, month").fetchall()


def investment_derived(row) -> dict:
    iv = row["india_value"] or 0.0
    ip = row["india_profit"] or 0.0
    gv = row["germany_value"] or 0.0
    gp = row["germany_profit"] or 0.0
    rate = row["eur_inr_rate"] or 0.0
    net_worth = iv + gv * rate
    profit = ip + gp * rate
    invested_base = net_worth - profit
    return {
        "net_worth_inr": net_worth,
        "profit_inr": profit,
        "india_pct": (ip / (iv - ip) * 100) if (iv - ip) else 0.0,
        "germany_pct": (gp / (gv - gp) * 100) if (gv - gp) else 0.0,
        "overall_pct": (profit / invested_base * 100) if invested_base else 0.0,
    }
