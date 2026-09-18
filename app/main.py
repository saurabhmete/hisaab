import io
import json
import os
import secrets
from datetime import date

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from . import database as d
from . import importer

app = FastAPI(title="Hisaab")
BASE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
templates.env.filters["eur"] = lambda v: f"€{v:,.0f}" if v is not None else "—"
templates.env.filters["eur2"] = lambda v: f"€{v:,.2f}".rstrip("0").rstrip(".") if v is not None else "—"
templates.env.filters["inr"] = lambda v: f"₹{v:,.0f}" if v is not None else "—"
templates.env.globals["month_names"] = d.MONTH_NAMES

SESSION_COOKIE = "hisaab_session"
PUBLIC_PATHS = ("/login", "/setup", "/static")

# First-run protection: creating the admin account requires this token, so a
# publicly reachable fresh install can't be claimed by a stranger. The token is
# printed to the server log (docker logs) — only the operator can read it.
SETUP_TOKEN = os.environ.get("HISAAB_SETUP_TOKEN") or secrets.token_urlsafe(16)


def _setup_token_ok(token: str) -> bool:
    return secrets.compare_digest(token or "", SETUP_TOKEN)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # HISAAB_AUTH=off disables the login wall (e.g. when Tailscale alone
        # decides who can reach the app). Everyone is then the admin.
        if os.environ.get("HISAAB_AUTH", "on").lower() == "off":
            request.state.user = {"id": 0, "username": "tailnet", "is_admin": 1}
            return await call_next(request)
        path = request.url.path
        if any(path == p or path.startswith(p + "/") or path.startswith("/static") for p in PUBLIC_PATHS):
            return await call_next(request)
        with d.get_db() as db:
            if d.user_count(db) == 0:
                return RedirectResponse("/setup", status_code=303)
            user = d.session_user(db, request.cookies.get(SESSION_COOKIE, ""))
        if user is None:
            return RedirectResponse("/login", status_code=303)
        request.state.user = dict(user)
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.on_event("startup")
def startup():
    d.init_db()
    with d.get_db() as db:
        if d.user_count(db) == 0:
            print(
                "\n"
                "==============================================================\n"
                " Hisaab first-run setup — create the admin account here:\n"
                f"   /setup?token={SETUP_TOKEN}\n"
                " (prepend your server's URL; this link is required so that\n"
                "  only you, not the public, can claim the admin account)\n"
                "==============================================================\n",
                flush=True,
            )


def _render(request, template, **ctx):
    with d.get_db() as db:
        ctx["years"] = d.all_years(db)
    ctx["request"] = request
    ctx["user"] = getattr(request.state, "user", None)
    ctx["today"] = date.today()
    return templates.TemplateResponse(template, ctx)


def _next_open_month(db):
    """(year, month) after the latest recorded month — or the current month."""
    last = d.latest_month(db)
    if not last:
        t = date.today()
        return t.year, t.month
    y, m = last["year"], last["month"]
    return (y + 1, 1) if m == 12 else (y, m + 1)


# ---------- auth ----------

@app.get("/setup")
def setup_page(request: Request, token: str = ""):
    with d.get_db() as db:
        if d.user_count(db) > 0:
            return RedirectResponse("/login", status_code=303)
    if not _setup_token_ok(token):
        return templates.TemplateResponse(
            "setup.html", {"request": request, "locked": True, "error": None},
            status_code=403,
        )
    return templates.TemplateResponse(
        "setup.html", {"request": request, "locked": False, "error": None, "token": token}
    )


@app.post("/setup")
def setup_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 password2: str = Form(...), token: str = Form("")):
    if not _setup_token_ok(token):
        return templates.TemplateResponse(
            "setup.html", {"request": request, "locked": True, "error": None},
            status_code=403,
        )
    error = None
    if len(password) < 8:
        error = "Password needs at least 8 characters."
    elif password != password2:
        error = "Passwords don't match."
    if error:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "locked": False, "error": error, "token": token},
        )
    with d.get_db() as db:
        if d.user_count(db) > 0:
            return RedirectResponse("/login", status_code=303)
        user_id = d.create_user(db, username, password, is_admin=True)
        token = d.create_session(db, user_id)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", max_age=90 * 86400)
    return resp


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    with d.get_db() as db:
        if d.recent_login_failures(db, ip) >= d.LOGIN_MAX_FAILURES:
            return templates.TemplateResponse(
                "login.html",
                {"request": request,
                 "error": f"Too many failed attempts — try again in {d.LOGIN_WINDOW_MINUTES} minutes."},
                status_code=429,
            )
        user = d.authenticate(db, username, password)
        if user is None:
            d.record_login_failure(db, ip)
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Wrong username or password."},
                status_code=401,
            )
        d.clear_login_failures(db, ip)
        token = d.create_session(db, user["id"])
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", max_age=90 * 86400)
    return resp


@app.post("/logout")
def logout(request: Request):
    with d.get_db() as db:
        d.delete_session(db, request.cookies.get(SESSION_COOKIE, ""))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- user management (admin only) ----------

def _require_admin(request):
    user = getattr(request.state, "user", None)
    return user if user and user["is_admin"] else None


@app.get("/users")
def users_page(request: Request, error: str = ""):
    if not _require_admin(request):
        return RedirectResponse("/", status_code=303)
    with d.get_db() as db:
        users = [dict(u) for u in d.all_users(db)]
    return _render(request, "users.html", users=users, error=error or None)


@app.post("/users")
def add_user(request: Request, username: str = Form(...), password: str = Form(...),
             is_admin: str = Form("")):
    if not _require_admin(request):
        return RedirectResponse("/", status_code=303)
    if len(password) < 8:
        return RedirectResponse("/users?error=Password+needs+at+least+8+characters.", status_code=303)
    try:
        with d.get_db() as db:
            d.create_user(db, username, password, is_admin=bool(is_admin))
    except Exception:
        return RedirectResponse("/users?error=That+username+is+taken.", status_code=303)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/delete")
def remove_user(request: Request, user_id: int):
    admin = _require_admin(request)
    if not admin:
        return RedirectResponse("/", status_code=303)
    with d.get_db() as db:
        target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if target and target["id"] != admin["id"]:
            # never delete the last admin
            if not (target["is_admin"] and d.admin_count(db) <= 1):
                d.delete_user(db, user_id)
    return RedirectResponse("/users", status_code=303)


# ---------- dashboard ----------

@app.get("/")
def dashboard(request: Request, year: int | None = None, month: int | None = None):
    with d.get_db() as db:
        if year and month:
            row = d.get_month(db, year, month)
        else:
            row = d.latest_month(db)
        current = None
        if row:
            current = {
                "row": dict(row),
                "expenses": [dict(e) for e in d.month_expenses(db, row["id"])],
                **d.month_summary(db, row),
            }
            year, month = row["year"], row["month"]
        else:
            t = date.today()
            year, month = year or t.year, month or t.month

        next_y, next_m = _next_open_month(db)

        trend = []
        for m in d.year_months(db, year):
            s = d.month_summary(db, m)
            trend.append({
                "label": d.MONTH_NAMES[m["month"] - 1][:3],
                "salary": m["salary"],
                "expense": s["total_expense"],
                "invested": m["invested"],
            })

        networth = []
        for inv in d.all_investments(db):
            der = d.investment_derived(inv)
            networth.append({
                "label": f"{d.MONTH_NAMES[inv['month'] - 1][:3]} {inv['year']}",
                "value": round(der["net_worth_inr"]),
                "profit": round(der["profit_inr"]),
            })

    return _render(
        request, "dashboard.html",
        current=current, sel_year=year, sel_month=month,
        next_y=next_y, next_m=next_m,
        trend_json=json.dumps(trend), networth_json=json.dumps(networth),
    )


@app.get("/goto")
def goto_month(year: int, month: int):
    return RedirectResponse(f"/month/{year}/{month}", status_code=303)


# ---------- month editor ----------

@app.get("/month/{year}/{month}")
def month_page(request: Request, year: int, month: int, saved: int = 0):
    with d.get_db() as db:
        row = d.get_month(db, year, month)
        prefilled = False
        if row:
            salary, invested = row["salary"], row["invested"]
            items = [
                {"category": e["category"], "amount": e["amount"]}
                for e in d.month_expenses(db, row["id"])
            ]
        else:
            # New month: prefill from the most recent recorded month so the
            # regulars (rent, gym, …) are one edit away instead of retyped.
            prev = d.previous_month_row(db, year, month)
            if prev:
                prefilled = True
                salary, invested = prev["salary"], prev["invested"]
                items = [
                    {"category": e["category"], "amount": e["amount"]}
                    for e in d.month_expenses(db, prev["id"])
                ]
            else:
                salary, invested = None, 0
                items = []
        summary = d.month_summary(db, row) if row else None
        cats = db.execute("SELECT DISTINCT category FROM expenses ORDER BY category").fetchall()
        rate_row = db.execute(
            "SELECT eur_inr_rate FROM investments ORDER BY year DESC, month DESC LIMIT 1"
        ).fetchone()
    return _render(
        request, "month.html",
        year=year, month=month, exists=row is not None, prefilled=prefilled,
        salary=salary, invested=invested, items=items, summary=summary,
        categories=[c["category"] for c in cats], saved=saved,
        eur_inr_rate=rate_row["eur_inr_rate"] if rate_row else None,
    )


@app.post("/month/{year}/{month}/save")
async def save_month(request: Request, year: int, month: int):
    form = await request.form()

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    salary = _num(form.get("salary"))
    invested = _num(form.get("invested"))
    names = form.getlist("category")
    amounts = form.getlist("amount")

    with d.get_db() as db:
        month_id = d.upsert_month(db, year, month, salary=salary, invested=invested)
        kept = {}
        for name, amount in zip(names, amounts):
            name = (name or "").strip()
            if name:
                kept[name.lower()] = (name, _num(amount))
        for name, amount in kept.values():
            d.set_expense(db, month_id, name, amount)
        # rows removed in the editor are removed from the month
        for e in d.month_expenses(db, month_id):
            if e["category"].lower() not in kept:
                d.delete_expense(db, e["id"])
    return RedirectResponse(f"/month/{year}/{month}?saved=1", status_code=303)


# ---------- years overview ----------

@app.get("/years")
def years_page(request: Request):
    with d.get_db() as db:
        summaries = []
        for y in d.all_years(db):
            months = d.year_months(db, y)
            if not months:
                continue
            total_salary = sum(m["salary"] for m in months)
            total_invested = sum(m["invested"] for m in months)
            total_expense = sum(d.month_summary(db, m)["total_expense"] for m in months)
            summaries.append({
                "year": y,
                "months": len(months),
                "salary": total_salary,
                "expense": total_expense,
                "invested": total_invested,
                "remainder": total_salary - total_expense - total_invested,
            })
    summaries.reverse()
    return _render(request, "years.html", summaries=summaries)


# ---------- year grid ----------

@app.get("/year/{year}")
def year_page(request: Request, year: int):
    with d.get_db() as db:
        months = [dict(m) for m in d.year_months(db, year)]
        categories = [
            r["category"] for r in db.execute(
                """SELECT DISTINCT e.category FROM expenses e
                   JOIN months m ON m.id = e.month_id WHERE m.year=?
                   ORDER BY e.category""", (year,)
            ).fetchall()
        ]
        grid = {}
        summaries = {}
        for m in d.year_months(db, year):
            for e in d.month_expenses(db, m["id"]):
                grid[(e["category"], m["month"])] = e["amount"]
            summaries[m["month"]] = d.month_summary(db, m)
    return _render(
        request, "year.html",
        year=year, months=months, categories=categories,
        grid=grid, summaries=summaries,
    )


# ---------- investments ----------

@app.get("/investments")
def investments_page(request: Request):
    with d.get_db() as db:
        rows = []
        for inv in d.all_investments(db):
            rows.append({**dict(inv), **d.investment_derived(inv)})
        last = rows[-1] if rows else None
    chart = [
        {
            "label": f"{d.MONTH_NAMES[r['month'] - 1][:3]} {str(r['year'])[2:]}",
            "networth": round(r["net_worth_inr"]),
            "profit": round(r["profit_inr"]),
            "india": r["india_value"] or 0,
            "germany": r["germany_value"] or 0,
            "overall_pct": round(r["overall_pct"], 2),
        }
        for r in rows
    ]
    t = date.today()
    return _render(request, "investments.html", rows=rows, last=last,
                   chart_json=json.dumps(chart), def_year=t.year, def_month=t.month)


@app.post("/investments")
def save_investment(
    year: int = Form(...), month: int = Form(...),
    india_value: float = Form(0), india_profit: float = Form(0),
    germany_value: float = Form(0), germany_profit: float = Form(0),
    eur_inr_rate: float = Form(110.0),
):
    with d.get_db() as db:
        d.upsert_investment(db, year, month, india_value, india_profit,
                            germany_value, germany_profit, eur_inr_rate)
    return RedirectResponse("/investments", status_code=303)


@app.post("/investments/{inv_id}/delete")
def remove_investment(inv_id: int):
    with d.get_db() as db:
        d.delete_investment(db, inv_id)
    return RedirectResponse("/investments", status_code=303)


# ---------- excel import ----------

@app.get("/import")
def import_page(request: Request):
    return _render(request, "import.html", report=None, error=None)


@app.post("/import")
async def import_excel(request: Request, file: UploadFile):
    error, report = None, None
    try:
        content = await file.read()
        with d.get_db() as db:
            report = importer.import_workbook(db, io.BytesIO(content))
    except Exception as exc:  # surface the reason in the UI, keep the app up
        error = f"Import failed: {exc}"
    return _render(request, "import.html", report=report, error=error)
