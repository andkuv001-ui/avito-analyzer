import httpx
import pytest
from supabase_auth.errors import AuthApiError

from src import sb_store
from src.sb_store import (
    StorageError,
    SupabaseNotConfigured,
    consume_quota,
    download_xlsx,
    get_quota,
    get_report,
    list_reports,
    save_report,
    sign_in,
    sign_out,
    sign_up,
    verify_token,
)

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


# --- фейки -------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class FakeRequest:
    def __init__(self, table, kind, payload=None):
        self.table = table
        self.kind = kind
        self.payload = payload
        self.filters = []
        self._order = None
        self._limit = None

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, size):
        self._limit = size
        return self

    def execute(self):
        if self.table.error is not None:
            raise self.table.error
        if self.kind == "insert":
            self.table.rows.append(dict(self.payload))
            return _Result([dict(self.payload)])
        rows = [
            dict(row) for row in self.table.rows
            if all(row.get(col) == value for col, value in self.filters)
        ]
        if self._order:
            column, desc = self._order
            rows.sort(key=lambda row: row.get(column), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.rows = store.setdefault(name, [])

    @property
    def error(self):
        return self.store.get("error")

    def insert(self, payload):
        return FakeRequest(self, "insert", payload)

    def select(self, columns):
        return FakeRequest(self, "select")


class FakeBucket:
    def __init__(self):
        self.files = {}
        self.error = None
        self.last_options = None

    def upload(self, path, data, options=None):
        if self.error is not None:
            raise self.error
        self.files[path] = bytes(data)
        self.last_options = options
        return _Result({"path": path})

    def download(self, path):
        if self.error is not None:
            raise self.error
        if path not in self.files:
            raise KeyError(path)
        return self.files[path]


class FakeAdmin:
    def __init__(self):
        self.tables = {}
        self.bucket = FakeBucket()
        self.rpc_calls = []
        self.rpc_result = True
        self.rpc_error = None

    def table(self, name):
        return FakeTable(self.tables, name)

    def rpc(self, name, payload):
        self.rpc_calls.append((name, payload))

        class _RPC:
            def execute(inner_self):
                if self.rpc_error is not None:
                    raise self.rpc_error
                return _Result(self.rpc_result)

        return _RPC()

    @property
    def storage(self):
        bucket = self.bucket

        class _Storage:
            def from_(self, name):
                return bucket

        return _Storage()


class FakeAuthAdmin:
    def __init__(self, owner):
        self.owner = owner

    def sign_out(self, jwt, scope="global"):
        self.owner.sign_out_calls.append((jwt, scope))


class FakeAuth:
    def __init__(self):
        self.admin = FakeAuthAdmin(self)
        self.sign_up_error = None
        self.sign_up_session = None
        self.sign_in_error = None
        self.get_user_error = None
        self.get_user_response = None
        self.sign_out_calls = []

    def sign_up(self, credentials):
        if self.sign_up_error is not None:
            raise self.sign_up_error
        user = type("User", (), {"id": USER, "email": credentials["email"]})()
        return type("AuthResponse", (), {"user": user, "session": self.sign_up_session})()

    def sign_in_with_password(self, credentials):
        if self.sign_in_error is not None:
            raise self.sign_in_error
        session = type("Session", (), {"access_token": "token-1"})()
        user = type("User", (), {"id": USER, "email": credentials["email"]})()
        return type("AuthResponse", (), {"session": session, "user": user})()

    def get_user(self, jwt):
        if self.get_user_error is not None:
            raise self.get_user_error
        return self.get_user_response


# --- квоты -------------------------------------------------------------------

def test_consume_quota_true_returns_true_and_calls_rpc():
    admin = FakeAdmin()
    assert consume_quota(USER, admin=admin) is True
    assert admin.rpc_calls == [("consume_report_quota", {"p_user": USER})]


def test_consume_quota_false():
    admin = FakeAdmin()
    admin.rpc_result = False
    assert consume_quota(USER, admin=admin) is False


def test_consume_quota_network_error_is_storage_error():
    admin = FakeAdmin()
    admin.rpc_error = httpx.ConnectError("boom")
    with pytest.raises(StorageError) as exc:
        consume_quota(USER, admin=admin)
    assert "Нет связи" in str(exc.value)


def test_get_quota_reads_profile():
    admin = FakeAdmin()
    admin.tables["profiles"] = [
        {"id": USER, "used_reports": 3, "quota_reports": 5},
        {"id": OTHER, "used_reports": 9, "quota_reports": 9},
    ]
    assert get_quota(USER, admin=admin) == (3, 5)


def test_get_quota_missing_profile_is_zero():
    assert get_quota(USER, admin=FakeAdmin()) == (0, 0)


# --- save_report ------------------------------------------------------------

def test_save_report_uploads_to_owner_path_and_inserts_row():
    admin = FakeAdmin()
    xlsx = b"PK\x03\x04 fake-xlsx"
    report_id = save_report(
        USER, "мох", "Москва", "openai/gpt-4o-mini", "выгрузка.xlsx",
        "# Отчёт", xlsx, "done", "warning-text", admin=admin,
    )
    path = f"reports/{USER}/{report_id}.xlsx"
    assert admin.bucket.files[path] == xlsx
    assert admin.bucket.last_options["content-type"].endswith("sheetml.sheet")

    rows = admin.tables["reports"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == report_id
    assert row["user_id"] == USER
    assert row["niche"] == "мох"
    assert row["region"] == "Москва"
    assert row["model"] == "openai/gpt-4o-mini"
    assert row["file_name"] == "выгрузка.xlsx"
    assert row["file_path"] == path
    assert row["status"] == "done"
    assert row["warning"] == "warning-text"
    assert row["report_md"] == "# Отчёт"


def test_save_report_without_xlsx_skips_upload():
    admin = FakeAdmin()
    report_id = save_report(
        USER, "мох", "Москва", None, None, "", None, "failed", None, admin=admin,
    )
    assert admin.bucket.files == {}
    assert admin.tables["reports"][0]["file_path"] is None
    assert admin.tables["reports"][0]["id"] == report_id


def test_save_report_network_error_is_storage_error():
    admin = FakeAdmin()
    admin.bucket.error = httpx.ConnectError("boom")
    with pytest.raises(StorageError) as exc:
        save_report(USER, "мох", "Москва", None, "a.xlsx", "", b"x", "done", None,
                    admin=admin)
    assert "Нет связи" in str(exc.value)


# --- list / get / download ---------------------------------------------------

def _seed_reports(admin):
    admin.tables["reports"] = [
        {"id": "r1", "user_id": USER, "niche": "мох", "region": "Москва",
         "status": "done", "created_at": "2026-10-01T10:00:00"},
        {"id": "r2", "user_id": USER, "niche": "кактус", "region": "Казань",
         "status": "failed", "created_at": "2026-10-03T10:00:00"},
        {"id": "r3", "user_id": OTHER, "niche": "чужой", "region": "Тверь",
         "status": "done", "created_at": "2026-10-05T10:00:00"},
    ]


def test_list_reports_only_own_newest_first():
    admin = FakeAdmin()
    _seed_reports(admin)
    rows = list_reports(USER, admin=admin)
    assert [row["id"] for row in rows] == ["r2", "r1"]


def test_get_report_filters_by_user():
    admin = FakeAdmin()
    _seed_reports(admin)
    assert get_report(USER, "r1", admin=admin)["niche"] == "мох"
    assert get_report(USER, "r3", admin=admin) is None
    assert get_report(USER, "missing", admin=admin) is None


def test_download_xlsx_roundtrip():
    admin = FakeAdmin()
    report_id = save_report(USER, "мох", "Москва", None, "a.xlsx", "", b"excel-bytes",
                            "done", None, admin=admin)
    assert download_xlsx(USER, report_id, admin=admin) == b"excel-bytes"


def test_download_xlsx_missing_report():
    with pytest.raises(StorageError) as exc:
        download_xlsx(USER, "nope", admin=FakeAdmin())
    assert "не найден" in str(exc.value)


def test_download_xlsx_without_file():
    admin = FakeAdmin()
    report_id = save_report(USER, "мох", "Москва", None, None, "", None,
                            "failed", None, admin=admin)
    with pytest.raises(StorageError) as exc:
        download_xlsx(USER, report_id, admin=admin)
    assert "не сохранён" in str(exc.value)


# --- auth --------------------------------------------------------------------

def test_sign_in_returns_session_and_user():
    session, user = sign_in("a@b.c", "secret", auth=FakeAuth())
    assert session.access_token == "token-1"
    assert user.id == USER


def test_sign_in_email_not_confirmed_is_friendly():
    auth = FakeAuth()
    auth.sign_in_error = AuthApiError("Email not confirmed", status=400,
                                      code="email_not_confirmed")
    with pytest.raises(StorageError) as exc:
        sign_in("a@b.c", "secret", auth=auth)
    assert "подтверждён" in str(exc.value)


def test_sign_in_wrong_password():
    auth = FakeAuth()
    auth.sign_in_error = AuthApiError("Invalid login credentials", status=400,
                                      code="invalid_credentials")
    with pytest.raises(StorageError) as exc:
        sign_in("a@b.c", "wrong", auth=auth)
    assert "Неверный email или пароль" in str(exc.value)


def test_sign_up_needs_confirmation_without_session():
    auth = FakeAuth()
    auth.sign_up_session = None
    result = sign_up("a@b.c", "secret1", auth=auth)
    assert result == {
        "user_id": USER, "email": "a@b.c", "needs_confirmation": True,
    }


def test_sign_up_duplicate_email():
    auth = FakeAuth()
    auth.sign_up_error = AuthApiError("User already registered", status=422,
                                      code="user_already_exists")
    with pytest.raises(StorageError) as exc:
        sign_up("a@b.c", "secret1", auth=auth)
    assert "уже зарегистрирован" in str(exc.value)


def test_verify_token_ok_and_invalid():
    auth = FakeAuth()
    auth.get_user_response = type("Resp", (), {"user": type("U", (), {"id": USER})()})()
    assert verify_token("good", auth=auth).id == USER

    auth.get_user_response = None
    assert verify_token("bad", auth=auth) is None

    auth.get_user_error = AuthApiError("bad jwt", status=401, code="bad_jwt")
    assert verify_token("bad", auth=auth) is None


def test_verify_token_network_error_raises_storage_error():
    auth = FakeAuth()
    auth.get_user_error = httpx.ConnectError("boom")
    with pytest.raises(StorageError):
        verify_token("good", auth=auth)


def test_sign_out_swallows_errors_and_calls_admin():
    auth = FakeAuth()
    sign_out("token-1", auth=auth)
    assert auth.sign_out_calls == [("token-1", "global")]

    auth.admin.sign_out = lambda jwt, scope="global": (_ for _ in ()).throw(RuntimeError("x"))
    sign_out("token-1", auth=auth)  # не должен raising


# --- конфигурация ------------------------------------------------------------

def test_missing_env_raises_not_configured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(SupabaseNotConfigured) as exc:
        sb_store.get_settings()
    assert "не настроен" in str(exc.value)
    sb_store.reset_clients()
    with pytest.raises(SupabaseNotConfigured):
        sb_store.get_auth_client()
