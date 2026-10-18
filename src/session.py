"""Чистые хелперы сессии и квот для app.py (без Streamlit — тестируются отдельно)."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime

from .sb_store import QUOTA_EXCEEDED_MESSAGE

STATUS_LABELS = {"done": "готов", "failed": "ошибка"}

# За сколько секунд до истечения access-token обновлять сессию
TOKEN_REFRESH_SKEW = 60


def normalize_session(raw: object) -> dict | None:
    """Сессия из st.session_state → dict | None.

    Обязательны `access_token` и `user_id`; `refresh_token`/`expires_at`
    опциональны (старые сессии без них работают как раньше).
    """
    if not isinstance(raw, dict):
        return None
    access_token = str(raw.get("access_token") or "").strip()
    user_id = str(raw.get("user_id") or "").strip()
    if not access_token or not user_id:
        return None
    try:
        expires_at = int(raw.get("expires_at") or 0) or None
    except (TypeError, ValueError):
        expires_at = None
    return {
        "access_token": access_token,
        "refresh_token": str(raw.get("refresh_token") or "").strip(),
        "expires_at": expires_at,
        "user_id": user_id,
        "email": str(raw.get("email") or "").strip(),
    }


def _jwt_exp(access_token: str) -> int | None:
    """Claim `exp` из payload JWT без проверки подписи (только тайминг)."""
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        exp = int(claims.get("exp") or 0) or None
    except Exception:
        return None
    return exp


def token_expired(raw: dict, now: float | None = None,
                  skew: int = TOKEN_REFRESH_SKEW) -> bool:
    """True — access-token истекает (с учётом запаса) и пора обновлять сессию.

    Источник срока: `expires_at` сессии, иначе `exp` из JWT payload.
    Срока нет/не разбирается → False (не трогаем).
    """
    expires_at = raw.get("expires_at") or _jwt_exp(str(raw.get("access_token") or ""))
    if not expires_at:
        return False
    if now is None:
        now = time.time()
    try:
        return float(now) >= float(expires_at) - skew
    except (TypeError, ValueError):
        return False


def format_quota(used: int, quota: int) -> str:
    return f"{used} / {quota}"


def quota_exceeded_message() -> str:
    return QUOTA_EXCEEDED_MESSAGE


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(str(status or ""), str(status or "—"))


def format_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    text = str(value or "")
    return text[:16].replace("T", " ") if text else "—"


def report_table_rows(reports: list[dict]) -> list[dict]:
    """Строки таблицы «Мои отчёты»: дата, ниша, регион, статус, warning."""
    rows = []
    for report in reports:
        rows.append({
            "Дата": format_date(report.get("created_at")),
            "Ниша": report.get("niche") or "—",
            "Регион": report.get("region") or "—",
            "Статус": status_label(report.get("status")),
            "Предупреждение": report.get("warning") or "",
        })
    return rows


def report_label(report: dict) -> str:
    warning = " ⚠" if report.get("warning") else ""
    return (
        f"{format_date(report.get('created_at'))} · "
        f"{report.get('niche') or '—'} · {report.get('region') or '—'} · "
        f"{status_label(report.get('status'))}{warning}"
    )
