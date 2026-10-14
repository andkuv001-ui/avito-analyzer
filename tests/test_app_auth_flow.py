from datetime import datetime

from src import session as sess
from src.sb_store import QUOTA_EXCEEDED_MESSAGE


def test_normalize_session_valid():
    raw = {"access_token": "tok", "user_id": "u1", "email": "a@b.c"}
    assert sess.normalize_session(raw) == {
        "access_token": "tok", "user_id": "u1", "email": "a@b.c",
    }


def test_normalize_session_rejects_partial_and_junk():
    assert sess.normalize_session(None) is None
    assert sess.normalize_session("tok") is None
    assert sess.normalize_session({}) is None
    assert sess.normalize_session({"access_token": "tok"}) is None
    assert sess.normalize_session({"user_id": "u1"}) is None
    assert sess.normalize_session({"access_token": "  ", "user_id": "u1"}) is None


def test_normalize_session_fills_missing_email():
    session = sess.normalize_session({"access_token": "tok", "user_id": "u1"})
    assert session["email"] == ""


def test_format_quota():
    assert sess.format_quota(1, 5) == "1 / 5"
    assert sess.format_quota(0, 0) == "0 / 0"


def test_quota_exceeded_message():
    assert sess.quota_exceeded_message() == QUOTA_EXCEEDED_MESSAGE
    assert "Лимит" in sess.quota_exceeded_message()


def test_status_label():
    assert sess.status_label("done") == "готов"
    assert sess.status_label("failed") == "ошибка"
    assert sess.status_label(None) == "—"
    assert sess.status_label("что-то") == "что-то"


def test_format_date():
    assert sess.format_date(datetime(2026, 10, 14, 15, 30)) == "14.10.2026 15:30"
    assert sess.format_date("2026-10-14T15:30:00+00:00") == "2026-10-14 15:30"
    assert sess.format_date(None) == "—"


def test_report_table_rows_columns_and_labels():
    rows = sess.report_table_rows([
        {"created_at": datetime(2026, 10, 1, 10, 0), "niche": "мох",
         "region": "Москва", "status": "done", "warning": None},
        {"created_at": datetime(2026, 10, 3, 10, 0), "niche": "кактус",
         "region": "Казань", "status": "failed", "warning": "grounding"},
    ])
    assert list(rows[0]) == ["Дата", "Ниша", "Регион", "Статус", "Предупреждение"]
    assert rows[0]["Статус"] == "готов"
    assert rows[0]["Предупреждение"] == ""
    assert rows[1]["Статус"] == "ошибка"
    assert rows[1]["Предупреждение"] == "grounding"


def test_report_label_marks_warning():
    clean = sess.report_label({"created_at": datetime(2026, 10, 1, 10, 0),
                               "niche": "мох", "region": "Москва", "status": "done"})
    assert "⚠" not in clean
    assert "01.10.2026 10:00" in clean
    assert "мох" in clean and "Москва" in clean and "готов" in clean

    warned = sess.report_label({"created_at": datetime(2026, 10, 1, 10, 0),
                                "niche": "мох", "region": "Москва", "status": "done",
                                "warning": "x"})
    assert "⚠" in warned
