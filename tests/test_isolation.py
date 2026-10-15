"""One person must never see another person's money.

The unit tests check the database layer; the HTTP test logs in as two different
people against the real app and looks for the first person's data in every page
the second person can open. Run with `python -m tests.test_isolation`.
"""
import http.cookiejar
import os
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from app import database as D
from tests import test_pages as P

SECRET_SALARY = "7777.77"
SECRET_CATEGORY = "TopSecretCategory"
SECRET_LOAN = "Admins Secret Loan"


def _fresh_db():
    D.DB_PATH = os.path.join(tempfile.mkdtemp(), "hisaab.db")
    D.init_db()
    return D.DB_PATH


# ---------- database layer ----------

def test_each_account_gets_its_own_data_file_and_the_first_keeps_the_original():
    _fresh_db()
    with D.get_auth_db() as auth:
        first = D.create_user(auth, "owner", "longenough1", True)
        second = D.create_user(auth, "partner", "longenough2", False)
        assert D.get_owner_id(auth) == first
        owner_path = D.data_path_for(auth, first)
        partner_path = D.data_path_for(auth, second)
    assert owner_path == D.DB_PATH and partner_path != D.DB_PATH and os.path.exists(partner_path)

    with D.get_db(owner_path) as db:
        D.set_expense(db, D.upsert_month(db, 2026, 1, salary=1234), SECRET_CATEGORY, 99)
    with D.get_db(partner_path) as db:
        assert D.all_years(db) == []
        assert db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0
        D.upsert_month(db, 2026, 1, salary=5)
    with D.get_db(owner_path) as db:      # the partner's write did not touch the owner's month
        assert db.execute("SELECT salary FROM months").fetchone()[0] == 1234


def test_opening_data_with_nobody_selected_fails_instead_of_picking_someones():
    _fresh_db()
    try:
        D.get_db()
    except RuntimeError as exc:
        assert "No user is selected" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_use_data_path_selects_a_file_only_inside_the_block():
    path = _fresh_db()
    with D.use_data_path(path):
        with D.get_db() as db:
            assert db.execute("SELECT COUNT(*) FROM months").fetchone()[0] == 0
    try:
        D.get_db()
    except RuntimeError:
        return
    raise AssertionError("selection leaked outside the block")


def test_removing_a_person_deletes_their_data_but_the_first_account_cannot_be_removed():
    _fresh_db()
    with D.get_auth_db() as auth:
        first = D.create_user(auth, "owner", "longenough1", True)
        second = D.create_user(auth, "partner", "longenough2", False)
        path = D.data_path_for(auth, second)
        assert os.path.exists(path)
        D.remove_user_data(auth, second)
        assert not os.path.exists(path)
        try:
            D.remove_user_data(auth, first)
        except ValueError:
            return
    raise AssertionError("the first account's data was removable")


def test_an_existing_database_gives_its_data_to_the_admin_not_to_whoever_was_added_later():
    path = _fresh_db()
    db = sqlite3.connect(path)
    db.execute("DROP TABLE meta")                           # how databases looked before accounts were separated
    db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('member', 'x', 0)")   # id 1
    db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', 'x', 1)")     # id 2
    db.execute("INSERT INTO months (year, month, salary, invested) VALUES (2026, 1, 4321, 0)")
    db.commit()
    db.close()

    D.init_db()
    with D.get_auth_db() as auth:
        assert D.get_owner_id(auth) == 2                     # the admin, even though the member has the lower id
        admin_path = D.data_path_for(auth, 2)
        member_path = D.data_path_for(auth, 1)
    assert admin_path == path
    with D.get_db(admin_path) as db:
        assert db.execute("SELECT salary FROM months").fetchone()[0] == 4321
    with D.get_db(member_path) as db:
        assert db.execute("SELECT COUNT(*) FROM months").fetchone()[0] == 0


# ---------- the real app, two real logins ----------

class Client:
    """A browser: keeps cookies, follows redirects."""

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=30) as r:
                return r.status, r.geturl(), r.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.geturl(), exc.read().decode()

    def get(self, path):
        return self._open(urllib.request.Request(self.base + path))

    def post(self, path, **form):
        data = urllib.parse.urlencode(form, doseq=True).encode()
        return self._open(urllib.request.Request(self.base + path, data=data))


def test_a_second_user_sees_none_of_the_first_users_data():
    previous = os.environ.get("HISAAB_AUTH")
    os.environ["HISAAB_AUTH"] = "on"
    try:
        server, port = P._start_server()
        from app.main import SETUP_TOKEN

        admin, partner = Client(port), Client(port)
        admin.post("/setup", username="admin", password="longenough1", password2="longenough1", token=SETUP_TOKEN)
        assert admin.get("/")[0] == 200

        # the admin fills in real-looking data of every kind
        admin.post("/month/2026/5/save", salary=SECRET_SALARY, invested="100",
                   category=[SECRET_CATEGORY], amount=["123"])
        admin.post("/investments", year="2026", month="5", india_value="555555", india_profit="1",
                   germany_value="9999", germany_profit="1", eur_inr_rate="100")
        admin.post("/loans", name=SECRET_LOAN, currency="EUR", principal="20000", start_date="2026-01-10",
                   rate="6", tenure_months="24")

        # the admin adds the partner, who logs in
        admin.post("/users", username="partner", password="longenough2")
        status, url, body = partner.post("/login", username="partner", password="longenough2")
        assert status == 200 and url.rstrip("/").endswith(str(port)), (status, url)

        pages = ["/", "/?year=2026&month=5", "/years", "/year/2026", "/month/2026/5", "/investments",
                 "/loans", "/loans/1", "/loans/1?close_on=2027-01-10", "/loans/new", "/loans/import",
                 "/import", "/month/2026/6"]
        for path in pages:
            status, url, body = partner.get(path)
            assert status == 200, (path, status)
            for secret in (SECRET_SALARY, SECRET_CATEGORY, SECRET_LOAN, "555,555", "9,999", "123.00"):
                assert secret not in body, f"{path} shows the admin's {secret!r} to another user"

        # the partner's own data stays theirs, too
        partner.post("/month/2026/5/save", salary="1111.11", invested="0", category=["PartnerOnly"], amount=["5"])
        assert "PartnerOnly" in partner.get("/month/2026/5")[2]
        for path in ("/", "/year/2026", "/month/2026/5", "/import"):
            assert "PartnerOnly" not in admin.get(path)[2], f"{path} shows the partner's data to the admin"

        # a member can't manage users
        status, url, body = partner.get("/users")
        assert "People with access" not in body

        # removing the partner deletes their data and logs them out at once
        partner_file = os.path.join(D.users_dir(), "2.db")
        assert os.path.exists(partner_file)
        admin.post("/users/2/delete")
        assert not os.path.exists(partner_file)
        status, url, body = partner.get("/")
        assert url.endswith("/login") and "Welcome back" in body
        # ...and the admin's data is untouched
        assert SECRET_CATEGORY in admin.get("/month/2026/5")[2]
    finally:
        if previous is None:
            os.environ.pop("HISAAB_AUTH", None)
        else:
            os.environ["HISAAB_AUTH"] = previous
        os.environ["HISAAB_AUTH"] = "off"      # the page tests expect login to be off


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
