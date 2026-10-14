"""Доступ к Supabase: аккаунты (Auth), история отчётов (Postgres), файлы (Storage).

Два клиента: `auth_client` (anon-key) — регистрация/вход/проверка токена;
`admin_client` (service-role) — PostgREST и Storage, только на сервере.
Все функции принимают клиенты параметрами (по умолчанию — лениво созданные),
чтобы тесты могли подставить фейки. Любая ошибка сети/сервиса превращается в
`StorageError` с текстом для пользователя, чтобы UI не падал traceback'ом.
"""

from __future__ import annotations

import os
import uuid

BUCKET = "reports"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
QUOTA_EXCEEDED_MESSAGE = "Лимит отчётов исчерпан — свяжитесь с нами, чтобы продолжить."


class StorageError(Exception):
    """Понятная пользователю ошибка сервиса аккаунтов (Supabase)."""


class SupabaseNotConfigured(StorageError):
    """Не заданы переменные окружения Supabase."""


_NETWORK_TYPE_NAMES = {
    "APIConnectionError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "ReadTimeout",
    "RemoteProtocolError",
    "WriteTimeout",
}


def get_settings() -> tuple[str, str, str]:
    url = os.getenv("SUPABASE_URL", "").strip()
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    service = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and anon and service):
        raise SupabaseNotConfigured(
            "Supabase не настроен: задайте SUPABASE_URL, SUPABASE_ANON_KEY "
            "и SUPABASE_SERVICE_ROLE_KEY (см. раздел «Настройка Supabase» в README)."
        )
    return url, anon, service


_auth_cache: dict = {}
_admin_cache: dict = {}


def get_auth_client():
    if not _auth_cache:
        from supabase import create_client

        url, anon, _ = get_settings()
        _auth_cache["client"] = create_client(url, anon)
    return _auth_cache["client"]


def get_admin_client():
    if not _admin_cache:
        from supabase import create_client

        url, _, service = get_settings()
        _admin_cache["client"] = create_client(url, service)
    return _admin_cache["client"]


def reset_clients() -> None:
    """Сбросить закэшированные клиенты (используется в тестах)."""
    _auth_cache.clear()
    _admin_cache.clear()


def _resolve(client, factory):
    return client if client is not None else factory()


def _is_network_error(exc: Exception) -> bool:
    if type(exc).__name__ in _NETWORK_TYPE_NAMES:
        return True
    return any(
        type(base).__name__ in _NETWORK_TYPE_NAMES
        for base in type(exc).__mro__
        if base is not Exception and base is not BaseException
    )


def _friendly(exc: Exception, action: str) -> StorageError:
    """Перевод ошибки Supabase в текст для пользователя."""
    if isinstance(exc, StorageError):
        return exc
    from supabase_auth.errors import AuthApiError

    code = str(getattr(exc, "code", "") or "")
    if isinstance(exc, AuthApiError):
        if code == "email_not_confirmed":
            return StorageError(
                "Email не подтверждён — откройте ссылку из письма и войдите снова."
            )
        if code == "invalid_credentials":
            return StorageError("Неверный email или пароль.")
        if code in ("user_already_exists", "email_exists"):
            return StorageError("Этот email уже зарегистрирован.")
        if code == "weak_password":
            return StorageError("Пароль слишком простой — нужен минимум 6 символов.")
        return StorageError(f"{action}: {exc}")
    if _is_network_error(exc):
        return StorageError("Нет связи с сервисом аккаунтов — попробуйте позже.")
    return StorageError(f"{action}: {exc}")


# --- Auth -------------------------------------------------------------------

def sign_up(email: str, password: str, auth=None) -> dict:
    """Регистрация. Возвращает {"user_id", "email", "needs_confirmation"}."""
    auth = _resolve(auth, get_auth_client)
    try:
        response = auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        raise _friendly(exc, "Не удалось зарегистрироваться") from exc
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    user_id = getattr(user, "id", None)
    if not user_id:
        raise StorageError("Не удалось зарегистрироваться: сервис не вернул пользователя.")
    return {
        "user_id": user_id,
        "email": getattr(user, "email", email),
        "needs_confirmation": session is None,
    }


def sign_in(email: str, password: str, auth=None) -> tuple[object, object]:
    """Вход. Возвращает (session, user). Ошибка входа → StorageError."""
    auth = _resolve(auth, get_auth_client)
    try:
        response = auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        raise _friendly(exc, "Не удалось войти") from exc
    session = getattr(response, "session", None)
    user = getattr(response, "user", None)
    if session is None or user is None:
        raise StorageError("Не удалось войти: сервис не вернул сессию.")
    return session, user


def verify_token(access_token: str, auth=None) -> object | None:
    """Проверка access-token. None — токен невалиден (выход из аккаунта)."""
    auth = _resolve(auth, get_auth_client)
    try:
        response = auth.get_user(access_token)
    except Exception as exc:
        if _is_network_error(exc):
            raise _friendly(exc, "Не удалось проверить вход") from exc
        return None
    user = getattr(response, "user", None) if response is not None else None
    return user or None


def sign_out(access_token: str, auth=None) -> None:
    """Отзыв refresh-токенов на сервере. Ошибка не мешает выйти локально."""
    auth = _resolve(auth, get_auth_client)
    try:
        auth.admin.sign_out(access_token, "global")
    except Exception:
        pass


# --- Квоты ------------------------------------------------------------------

def consume_quota(user_id: str, admin=None) -> bool:
    """Атомарное списание одного отчёта (RPC, ленивый сброс 30-дневного окна)."""
    admin = _resolve(admin, get_admin_client)
    try:
        response = admin.rpc("consume_report_quota", {"p_user": user_id}).execute()
    except Exception as exc:
        raise _friendly(exc, "Не удалось проверить лимит отчётов") from exc
    return bool(response.data)


def get_quota(user_id: str, admin=None) -> tuple[int, int]:
    """(used, quota) из профиля — для счётчика в сайдбаре."""
    admin = _resolve(admin, get_admin_client)
    try:
        response = (
            admin.table("profiles")
            .select("used_reports,quota_reports")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise _friendly(exc, "Не удалось получить лимит отчётов") from exc
    rows = response.data or []
    if not rows:
        return 0, 0
    return int(rows[0].get("used_reports") or 0), int(rows[0].get("quota_reports") or 0)


# --- История отчётов --------------------------------------------------------

def save_report(user_id: str, niche: str, region: str, model: str | None,
                file_name: str | None, report_md: str, xlsx_bytes: bytes | None,
                status: str, warning: str | None, admin=None) -> str:
    """Upload Excel в Storage + insert метаданных/Markdown в reports.

    report_id генерируется заранее: путь в Storage — `reports/{user}/{id}.xlsx`.
    """
    admin = _resolve(admin, get_admin_client)
    report_id = str(uuid.uuid4())
    file_path = f"{BUCKET}/{user_id}/{report_id}.xlsx"
    if xlsx_bytes:
        try:
            admin.storage.from_(BUCKET).upload(
                file_path, xlsx_bytes, {"content-type": XLSX_CONTENT_TYPE}
            )
        except Exception as exc:
            raise _friendly(exc, "Не удалось сохранить Excel-файл") from exc
    row = {
        "id": report_id,
        "user_id": user_id,
        "niche": niche,
        "region": region,
        "model": model,
        "file_name": file_name,
        "file_path": file_path if xlsx_bytes else None,
        "status": status,
        "warning": warning,
        "report_md": report_md,
    }
    try:
        admin.table("reports").insert(row).execute()
    except Exception as exc:
        raise _friendly(exc, "Не удалось сохранить отчёт") from exc
    return report_id


def list_reports(user_id: str, admin=None) -> list[dict]:
    """Отчёты пользователя, новые сверху."""
    admin = _resolve(admin, get_admin_client)
    try:
        response = (
            admin.table("reports")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise _friendly(exc, "Не удалось загрузить историю отчётов") from exc
    return list(response.data or [])


def get_report(user_id: str, report_id: str, admin=None) -> dict | None:
    """Один отчёт; чужой report_id → None (фильтр по user_id)."""
    admin = _resolve(admin, get_admin_client)
    try:
        response = (
            admin.table("reports")
            .select("*")
            .eq("id", report_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise _friendly(exc, "Не удалось открыть отчёт") from exc
    rows = response.data or []
    return rows[0] if rows else None


def download_xlsx(user_id: str, report_id: str, admin=None) -> bytes:
    """Скачать исходный Excel из приватного bucket (только файлы владельца)."""
    admin = _resolve(admin, get_admin_client)
    report = get_report(user_id, report_id, admin=admin)
    if report is None:
        raise StorageError("Отчёт не найден.")
    file_path = report.get("file_path")
    if not file_path:
        raise StorageError("Для этого отчёта Excel-файл не сохранён.")
    try:
        return admin.storage.from_(BUCKET).download(file_path)
    except Exception as exc:
        raise _friendly(exc, "Не удалось скачать Excel") from exc
