"""SQLite layer for Hisaab: stdlib sqlite3, no ORM.

Two kinds of database:
* the accounts database (DB_PATH): users, sessions, login attempts, and one
  `meta` table. It also holds the original data of the first account.
* one data database per person: months, expenses, investments and loans.
  The first account keeps using DB_PATH; every other account gets its own file
  in a `users/` folder next to it. Which file a request opens is decided once,
  from the logged-in user, so one person's queries can never reach another's.
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar

DB_PATH = os.environ.get(
    "HISAAB_DB", os.path.join(os.path.dirname(__file__), "..", "data", "hisaab.db")
)

_ALL_TABLES_SQL = """
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

CREATE TABLE IF NOT EXISTS loans (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    lender        TEXT NOT NULL DEFAULT '',
    currency      TEXT NOT NULL DEFAULT 'EUR',
    principal     REAL NOT NULL CHECK (principal > 0),
    start_date    TEXT NOT NULL,
    tenure_months INTEGER,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    rate_applies  TEXT NOT NULL DEFAULT 'on_emi_date'
);

CREATE TABLE IF NOT EXISTS loan_emis (
    id         INTEGER PRIMARY KEY,
    loan_id    INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    amount     REAL NOT NULL CHECK (amount > 0),
    UNIQUE (loan_id, start_date)
);

CREATE TABLE IF NOT EXISTS loan_rates (
    id         INTEGER PRIMARY KEY,
    loan_id    INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    rate       REAL NOT NULL CHECK (rate >= 0),
    UNIQUE (loan_id, start_date)
);

CREATE TABLE IF NOT EXISTS loan_prepayments (
    id      INTEGER PRIMARY KEY,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    date    TEXT NOT NULL,
    amount  REAL NOT NULL CHECK (amount > 0),
    note    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS loan_installments (
    id          INTEGER PRIMARY KEY,
    loan_id     INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    due_date    TEXT NOT NULL,
    emi         REAL NOT NULL,
    principal   REAL NOT NULL,
    interest    REAL NOT NULL,
    prepaid     REAL NOT NULL DEFAULT 0,
    capitalized REAL NOT NULL DEFAULT 0,
    closing     REAL NOT NULL,
    UNIQUE (loan_id, due_date)
);

CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY,
    ip TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

AUTH_TABLES = {"users", "sessions", "login_failures", "meta"}


def _split_schema(sql):
    parts = [p.strip() for p in sql.strip().split("\n\n") if p.strip()]
    auth = [p for p in parts if re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", p).group(1) in AUTH_TABLES]
    data = [p for p in parts if p not in auth]
    return "\n\n".join(auth) + "\n", "\n\n".join(data) + "\n"


AUTH_SCHEMA, DATA_SCHEMA = _split_schema(_ALL_TABLES_SQL)
SCHEMA = _ALL_TABLES_SQL            # everything: the main file holds the accounts and the first person's data

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def init_db():
    """Create/upgrade the accounts database (which is also the first account's data file)."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with get_auth_db() as db:
        db.executescript(SCHEMA)
    _drop_loan_currency_check(DB_PATH)
    _add_loan_columns(DB_PATH)
    with get_auth_db() as db:
        _ensure_owner(db)


def _add_loan_columns(path):
    """Columns added after the first release of the loans table."""
    db = sqlite3.connect(path)
    try:
        have = {r[1] for r in db.execute("PRAGMA table_info(loans)")}
        if have and "rate_applies" not in have:
            db.execute("ALTER TABLE loans ADD COLUMN rate_applies TEXT NOT NULL DEFAULT 'on_emi_date'")
            db.commit()
    finally:
        db.close()


def _drop_loan_currency_check(path):
    """Early databases created `loans` with CHECK (currency IN ('EUR','INR')).
    SQLite can't drop a constraint in place, so rebuild the table once, with
    foreign keys off so the child tables' rows aren't cascade-deleted."""
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT sql FROM sqlite_master WHERE name='loans'").fetchone()
        if not row or "currency IN" not in row[0]:
            return
        db.execute("PRAGMA foreign_keys = OFF")
        db.executescript("""
            BEGIN;
            CREATE TABLE loans_new (
                id            INTEGER PRIMARY KEY,
                name          TEXT NOT NULL,
                lender        TEXT NOT NULL DEFAULT '',
                currency      TEXT NOT NULL DEFAULT 'EUR',
                principal     REAL NOT NULL CHECK (principal > 0),
                start_date    TEXT NOT NULL,
                tenure_months INTEGER,
                notes         TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO loans_new
                SELECT id, name, lender, currency, principal, start_date,
                       tenure_months, notes, created_at FROM loans;
            DROP TABLE loans;
            ALTER TABLE loans_new RENAME TO loans;
            COMMIT;
        """)
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("loans table rebuild left dangling references")
    finally:
        db.close()


# ---------- connections ----------

_data_path = ContextVar("hisaab_data_path", default=None)


def _connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def _session(path):
    db = _connect(path)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def get_auth_db():
    """The accounts database: users, sessions, login attempts."""
    return _session(DB_PATH)


def get_db(path=None):
    """The data database of whoever is being served right now.

    The request middleware picks the file from the logged-in user. Code that runs
    outside a request must say which file explicitly (`path=`, or `use_data_path`).
    There is deliberately no fallback: with nobody selected this raises instead of
    opening someone else's data.
    """
    path = path or _data_path.get()
    if path is None:
        raise RuntimeError("No user is selected, so there is no data to open.")
    return _session(path)


def set_data_path(path):
    _data_path.set(path)


@contextmanager
def use_data_path(path):
    token = _data_path.set(path)
    try:
        yield
    finally:
        _data_path.reset(token)


def users_dir():
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "users")


def get_owner_id(auth_db):
    """Id of the account whose data lives in the main file (the first account)."""
    row = auth_db.execute("SELECT value FROM meta WHERE key='data_owner'").fetchone()
    return int(row["value"]) if row else None


def _ensure_owner(auth_db):
    """Databases from before accounts were separated: the data in the main file
    belongs to the admin who set the app up (the oldest admin account)."""
    if get_owner_id(auth_db) is None:
        row = auth_db.execute("SELECT id FROM users ORDER BY is_admin DESC, id LIMIT 1").fetchone()
        if row:
            auth_db.execute("INSERT INTO meta (key, value) VALUES ('data_owner', ?)", (str(row["id"]),))


def data_path_for(auth_db, user_id):
    """Path of this person's data database, creating it the first time.
    user_id 0 is the pseudo-user used when login is switched off."""
    if user_id == 0 or user_id == get_owner_id(auth_db):
        return DB_PATH
    path = os.path.join(users_dir(), f"{int(user_id)}.db")
    if not os.path.exists(path):
        init_data_db(path)
    return path


def init_data_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = _connect(path)
    try:
        db.executescript(DATA_SCHEMA)
        db.commit()
    finally:
        db.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def remove_user_data(auth_db, user_id):
    """Delete a person's data files. The first account's data can't be removed this way."""
    if user_id == get_owner_id(auth_db):
        raise ValueError("The first account holds the original data and can't be removed.")
    base = os.path.join(users_dir(), f"{int(user_id)}.db")
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(base + suffix)
        except FileNotFoundError:
            pass


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
    if get_owner_id(db) is None:        # the very first account keeps the original data file
        db.execute("INSERT INTO meta (key, value) VALUES ('data_owner', ?)", (str(cur.lastrowid),))
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
