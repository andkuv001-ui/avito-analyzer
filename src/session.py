"""Чистые хелперы сессии и квот для app.py (без Streamlit — тестируются отдельно)."""

from __future__ import annotations

from datetime import datetime

from .sb_store import QUOTA_EXCEEDED_MESSAGE

STATUS_LABELS = {"done": "готов", "failed": "ошибка"}


def normalize_session(raw: object) -> dict | None:
    """Сессия из st.session_state → {"access_token", "user_id", "email"} | None."""
    if not isinstance(raw, dict):
        return None
    access_token = str(raw.get("access_token") or "").strip()
    user_id = str(raw.get("user_id") or "").strip()
    if not access_token or not user_id:
        return None
    return {
        "access_token": access_token,
        "user_id": user_id,
        "email": str(raw.get("email") or "").strip(),
    }


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
