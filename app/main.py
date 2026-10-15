import io
import json
import os
import secrets
from datetime import date

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from . import data_import
from . import database as d
from . import importer
from . import loan_import
from . import loans
from . import rates

app = FastAPI(title="Hisaab")
BASE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
templates.env.filters["eur"] = lambda v: f"€{v:,.0f}" if v is not None else "—"
templates.env.filters["eur2"] = lambda v: f"€{v:,.2f}".rstrip("0").rstrip(".") if v is not None else "—"
templates.env.filters["inr"] = lambda v: f"₹{v:,.0f}" if v is not None else "—"
def _money(value, currency="EUR", decimals=0):
    if value is None:
        return "—"
    symbol = loans.CURRENCIES.get(currency, f"{currency} ")
    return f"{symbol}{value:,.{decimals}f}"


templates.env.filters["money"] = _money
templates.env.globals["month_names"] = d.MONTH_NAMES
templates.env.globals["currencies"] = loans.CURRENCIES
templates.env.globals["rate_timings"] = loans.RATE_TIMINGS

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

        # EMIs of euro loans that fall due this month, offered as expense rows.
        # (INR loans are skipped: the planner is in euros.)
        planned = {i["category"].lower(): i["amount"] for i in items}
        loan_emis = []
        for ln in loans.all_loans(db):
            if ln["currency"] != "EUR":
                continue
            due = next(
                (r for r in loans.load(db, ln, date.today())["rows"]
                 if r["due"].year == year and r["due"].month == month),
                None,
            )
            if due is None:
                continue
            category = f"EMI: {ln['name']}"
            amount = round(due["emi"], 2)
            current = planned.get(category.lower())
            if current is None or abs(current - amount) > 0.005:
                loan_emis.append({"category": category, "amount": amount,
                                  "replaces": current is not None})
    return _render(
        request, "month.html",
        year=year, month=month, exists=row is not None, prefilled=prefilled,
        salary=salary, invested=invested, items=items, summary=summary,
        categories=[c["category"] for c in cats], saved=saved,
        eur_inr_rate=rate_row["eur_inr_rate"] if rate_row else None,
        loan_emis=loan_emis,
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
    live_rate = rates.eur_inr()
    # Headline cards: latest holdings always valued at today's rate (saved rate
    # only as offline fallback). Historical rows keep the rate they were saved
    # with — the past isn't repainted when the currency moves.
    head = None
    if last:
        head = dict(last)
        if live_rate:
            head["eur_inr_rate"] = live_rate
            head.update(d.investment_derived(head))
    return _render(request, "investments.html", rows=rows, last=last, head=head,
                   chart_json=json.dumps(chart), def_year=t.year, def_month=t.month,
                   live_rate=live_rate)


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


# ---------- loans ----------

def _loan_values(name, lender, currency, principal, start_date, tenure_months, notes,
                 rate_applies="on_emi_date"):
    """Validate the fields shared by create and edit. Raises ValueError with a
    message fit to show the user."""
    name = name.strip()
    if not name:
        raise ValueError("Give the loan a name.")
    if currency not in loans.CURRENCIES:
        raise ValueError("Pick one of the listed currencies.")
    if principal <= 0:
        raise ValueError("The loan amount must be greater than zero.")
    try:
        date.fromisoformat(start_date)
    except ValueError:
        raise ValueError("The start date is not a valid date.")
    if rate_applies not in loans.RATE_TIMINGS:
        raise ValueError("Pick when rate changes apply from the list.")
    tenure = None
    if tenure_months.strip():
        try:
            tenure = int(float(tenure_months))
        except ValueError:
            raise ValueError("Tenure must be a whole number of months.")
        if not 0 < tenure <= loans.MAX_MONTHS:
            raise ValueError(f"Tenure must be between 1 and {loans.MAX_MONTHS} months.")
    return {"name": name, "lender": lender, "currency": currency, "principal": principal,
            "start_date": start_date, "tenure_months": tenure, "notes": notes,
            "rate_applies": rate_applies}


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


@app.get("/loans")
def loans_page(request: Request):
    today = date.today()
    with d.get_db() as db:
        items = [loans.load(db, ln, today) for ln in loans.all_loans(db)]
    totals = {}
    for it in items:
        s = it["summary"]
        if not s["closed"]:
            t = totals.setdefault(it["loan"]["currency"], {"outstanding": 0.0, "emi": 0.0})
            t["outstanding"] += s["outstanding"]
            t["emi"] += s["current_emi"] or 0
    return _render(request, "loans.html", items=items, totals=totals)


@app.get("/loans/new")
def loan_new(request: Request):
    form = {"currency": "EUR", "start_date": date.today().isoformat()}
    return _render(request, "loan_new.html", form=form, error=None)


MAX_IMPORT_BYTES = 1_000_000


async def _upload_text(file, pasted):
    """Text of an uploaded file (preferred) or pasted box; None if the file is too big."""
    if file is not None and file.filename:
        content = await file.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            return None
        return content.decode("utf-8-sig", errors="replace")
    return pasted


def _import_page(request, result=None, pasted=""):
    return _render(request, "loan_import.html", prompt=loan_import.build_prompt(date.today()),
                   result=result, pasted=pasted)


@app.get("/loans/import")
def loan_import_page(request: Request):
    return _import_page(request)


@app.post("/loans/import")
async def loan_import_check(request: Request, file: UploadFile | None = File(None),
                            pasted: str = Form("")):
    """Step 1 of 2: validate the file and show what would be created. Saves nothing."""
    text = await _upload_text(file, pasted)
    if text is None:
        result = {"ok": False, "errors": ["That file is too large to be a loan import."],
                  "warnings": [], "info": []}
        return _import_page(request, result)
    result = loan_import.parse(text, date.today())
    if not result["ok"]:
        return _import_page(request, result, pasted=text if len(text) < 200_000 else "")
    with d.get_db() as db:
        same_name = db.execute("SELECT 1 FROM loans WHERE lower(name)=lower(?)",
                               (result["data"]["loan"]["name"],)).fetchone()
    if same_name:
        result["warnings"].append(
            f"You already have a loan called '{result['data']['loan']['name']}'. "
            "Importing again creates a second one.")
    return _render(request, "loan_import_preview.html", result=result, payload=text)


@app.post("/loans/import/confirm")
def loan_import_confirm(request: Request, payload: str = Form(...)):
    """Step 2 of 2: re-validate the same text and store it."""
    result = loan_import.parse(payload, date.today())
    if not result["ok"]:
        return _import_page(request, result, pasted=payload)
    with d.get_db() as db:
        loan_id = loan_import.create(db, result["data"])
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans")
def loan_create(
    request: Request, name: str = Form(""), lender: str = Form(""), currency: str = Form("EUR"),
    principal: float = Form(0), start_date: str = Form(""), rate: float = Form(0),
    tenure_months: str = Form(""), emi: str = Form(""), notes: str = Form(""),
):
    form = {"name": name, "lender": lender, "currency": currency, "principal": principal or "",
            "start_date": start_date, "rate": rate, "tenure_months": tenure_months,
            "emi": emi, "notes": notes}
    try:
        values = _loan_values(name, lender, currency, principal, start_date, tenure_months, notes)
        if rate < 0:
            raise ValueError("The interest rate can't be negative.")
        if emi.strip():
            try:
                emi_amount = float(emi)
            except ValueError:
                raise ValueError("The EMI must be a number.")
        elif values["tenure_months"]:
            emi_amount = round(loans.calc_emi(principal, rate, values["tenure_months"]), 2)
        else:
            raise ValueError("Enter the EMI, or the tenure so it can be calculated.")
        if emi_amount <= 0:
            raise ValueError("The EMI must be greater than zero.")
    except ValueError as exc:
        return _render(request, "loan_new.html", form=form, error=str(exc))
    with d.get_db() as db:
        loan_id = loans.create_loan(
            db, values["name"], values["lender"], values["currency"], values["principal"],
            values["start_date"], values["tenure_months"], values["notes"], emi_amount, rate,
        )
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.get("/loans/{loan_id}")
def loan_detail(request: Request, loan_id: int, error: str = "",
                close_on: str = "", close_charge: float = 0.0):
    today = date.today()
    with d.get_db() as db:
        loan = loans.get_loan(db, loan_id)
        if loan is None:
            return RedirectResponse("/loans", status_code=303)
        data = loans.load(db, loan, today)
    rows, s = data["rows"], data["summary"]

    # Balance over time: solid for installments already due, dashed for the projection.
    labels = [loans.parse_date(loan["start_date"]).strftime("%b %Y")]
    values = [loan["principal"]]
    paid_flags = [True]
    for r in rows:
        labels.append(r["due"].strftime("%b %Y"))
        values.append(round(r["closing"], 2))
        paid_flags.append(r["paid"])
    last_paid = max((i for i, p in enumerate(paid_flags) if p), default=0)
    chart = {
        "labels": labels,
        "paid": [v if i <= last_paid else None for i, v in enumerate(values)],
        "projected": [v if i >= last_paid else None for i, v in enumerate(values)],
        "yearly": [
            {"year": y["year"], "principal": round(y["principal"], 2), "interest": round(y["interest"], 2)}
            for y in loans.yearly(rows)
        ],
        "symbol": loans.CURRENCIES.get(loan["currency"], loan["currency"] + " "),
    }
    closing, closing_error = None, None
    if close_on:
        try:
            when = date.fromisoformat(close_on)
            if not 0 <= close_charge <= 25:
                raise ValueError("The foreclosure charge should be between 0% and 25%.")
            closing = loans.foreclosure(data["loan"], rows, data["rate_timeline"],
                                        data["prepayments"], when, close_charge)
        except ValueError as exc:
            closing_error = ("That isn't a valid date." if "isoformat" in str(exc) or "Invalid isoformat" in str(exc)
                             else str(exc))
    default_close = (s["next"]["due"] if s["next"] else today).isoformat()
    return _render(
        request, "loan_detail.html", **data, chart_json=json.dumps(chart),
        today=today, error=error or None, closing=closing, closing_error=closing_error,
        close_on=close_on or default_close, close_charge=close_charge,
    )


@app.post("/loans/{loan_id}/edit")
def loan_edit(
    loan_id: int, name: str = Form(""), lender: str = Form(""), currency: str = Form("EUR"),
    principal: float = Form(0), start_date: str = Form(""), tenure_months: str = Form(""),
    notes: str = Form(""), rate_applies: str = Form("on_emi_date"),
):
    try:
        v = _loan_values(name, lender, currency, principal, start_date, tenure_months, notes,
                         rate_applies)
    except ValueError as exc:
        return RedirectResponse(f"/loans/{loan_id}?error={str(exc).replace(' ', '+')}", status_code=303)
    with d.get_db() as db:
        loans.update_loan(db, loan_id, v["name"], v["lender"], v["currency"], v["principal"],
                          v["start_date"], v["tenure_months"], v["notes"], v["rate_applies"])
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans/{loan_id}/delete")
def loan_delete(loan_id: int):
    with d.get_db() as db:
        loans.delete_loan(db, loan_id)
    return RedirectResponse("/loans", status_code=303)


def _record_period(kind, loan_id, start_date, end_date, value, value_ok):
    """Shared by the EMI and rate forms; `end_date` empty means 'from now on'."""
    end_date = end_date.strip()
    problem = None
    if not _valid_date(start_date) or (end_date and not _valid_date(end_date)):
        problem = "That date isn't valid."
    elif not value_ok:
        problem = "That value isn't valid."
    else:
        try:
            with d.get_db() as db:
                loans.set_period(db, kind, loan_id, start_date, end_date or None, value)
        except ValueError as exc:
            problem = str(exc)
    if problem:
        return RedirectResponse(f"/loans/{loan_id}?error={problem.replace(' ', '+')}", status_code=303)
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans/{loan_id}/emi")
def loan_emi_add(loan_id: int, start_date: str = Form(...), amount: float = Form(...),
                 end_date: str = Form("")):
    return _record_period("emi", loan_id, start_date, end_date, amount, amount > 0)


@app.post("/loans/{loan_id}/emi/{emi_id}/delete")
def loan_emi_delete(loan_id: int, emi_id: int):
    with d.get_db() as db:
        loans.delete_emi(db, loan_id, emi_id)
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans/{loan_id}/rate")
def loan_rate_add(loan_id: int, start_date: str = Form(...), rate: float = Form(...),
                  end_date: str = Form("")):
    return _record_period("rate", loan_id, start_date, end_date, rate, rate >= 0)


@app.post("/loans/{loan_id}/rate/{rate_id}/delete")
def loan_rate_delete(loan_id: int, rate_id: int):
    with d.get_db() as db:
        loans.delete_rate(db, loan_id, rate_id)
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans/{loan_id}/prepay")
def loan_prepay_add(loan_id: int, when: str = Form(..., alias="date"), amount: float = Form(...),
                    note: str = Form("")):
    if _valid_date(when) and amount > 0:
        with d.get_db() as db:
            loans.add_prepayment(db, loan_id, when, amount, note)
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/loans/{loan_id}/prepay/{prepay_id}/delete")
def loan_prepay_delete(loan_id: int, prepay_id: int):
    with d.get_db() as db:
        loans.delete_prepayment(db, loan_id, prepay_id)
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


# ---------- excel import ----------

def _known_categories(db):
    return [r["category"] for r in db.execute("SELECT DISTINCT category FROM expenses ORDER BY category")]


def _import_data_page(request, report=None, error=None, result=None, pasted=""):
    with d.get_db() as db:
        categories = _known_categories(db)
    return _render(request, "import.html", report=report, error=error, result=result, pasted=pasted,
                   prompt=data_import.build_prompt(date.today(), categories))


@app.get("/import")
def import_page(request: Request):
    return _import_data_page(request)


@app.post("/import")
async def import_excel(request: Request, file: UploadFile):
    error, report = None, None
    try:
        content = await file.read()
        with d.get_db() as db:
            report = importer.import_workbook(db, io.BytesIO(content))
    except Exception as exc:  # surface the reason in the UI, keep the app up
        error = f"Import failed: {exc}"
    return _import_data_page(request, report=report, error=error)


@app.post("/import/assisted")
async def import_assisted_check(request: Request, file: UploadFile | None = File(None),
                                pasted: str = Form("")):
    """Step 1 of 2: validate a Claude-made file and show what would change. Saves nothing."""
    text = await _upload_text(file, pasted)
    if text is None:
        result = {"ok": False, "errors": ["That file is too large to be a data import."],
                  "warnings": [], "info": []}
        return _import_data_page(request, result=result)
    with d.get_db() as db:
        result = data_import.parse(text, date.today(), _known_categories(db))
        if not result["ok"]:
            return _import_data_page(request, result=result,
                                     pasted=text if len(text) < 200_000 else "")
        change = data_import.describe(db, result["data"])
    result["warnings"] += change["notes"]
    return _render(request, "data_import_preview.html", result=result, change=change, payload=text)


@app.post("/import/assisted/confirm")
def import_assisted_confirm(request: Request, payload: str = Form(...)):
    """Step 2 of 2: re-validate the same text and store it."""
    with d.get_db() as db:
        result = data_import.parse(payload, date.today(), _known_categories(db))
        if not result["ok"]:
            return _import_data_page(request, result=result, pasted=payload)
        report = data_import.create(db, result["data"])
    return _import_data_page(request, report=report)
