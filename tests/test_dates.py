from datetime import datetime
from pathlib import Path

import pytest

from src.dates import NO_FRESHNESS, parse_published

REF = datetime(2026, 10, 1, 18, 0)
REF_JULY = datetime(2026, 7, 15, 12, 0)


def test_today_with_time():
    assert parse_published("сегодня в 12:35", REF) == datetime(2026, 10, 1, 12, 35)


def test_yesterday_with_time():
    assert parse_published("вчера в 12:26", REF) == datetime(2026, 9, 30, 12, 26)


def test_human_date_current_year():
    assert parse_published("7 сентября в 13:27", REF) == datetime(2026, 9, 7, 13, 27)


def test_human_date_past_this_year():
    assert parse_published("31 августа в 09:15", REF) == datetime(2026, 8, 31, 9, 15)


def test_year_rollover_future_date():
    assert parse_published("31 августа в 10:00", REF_JULY) == datetime(2025, 8, 31, 10, 0)


def test_year_rollover_future_day_month():
    assert parse_published("2 октября в 11:00", REF) == datetime(2025, 10, 2, 11, 0)


def test_today_no_time():
    assert parse_published("сегодня", REF) == datetime(2026, 10, 1, 0, 0)


def test_yesterday_no_time():
    assert parse_published("вчера", REF) == datetime(2026, 9, 30, 0, 0)


def test_case_and_yo_insensitive():
    assert parse_published("7 СЕНТЯБРЯ в 13:27", REF) == datetime(2026, 9, 7, 13, 27)
    assert parse_published("31 августа в 09:15", REF) == datetime(2026, 8, 31, 9, 15)


def test_numeric_dmy():
    assert parse_published("28.09.2026 19:12", REF) == datetime(2026, 9, 28, 19, 12)
    assert parse_published("28.09.2026", REF) == datetime(2026, 9, 28, 0, 0)


def test_iso_format():
    assert parse_published("2026-09-25 14:00", REF) == datetime(2026, 9, 25, 14, 0)


def test_datetime_passthrough():
    dt = datetime(2026, 9, 25, 14, 0)
    assert parse_published(dt, REF) is dt


def test_unrecognized_returns_none():
    assert parse_published("когда-то", REF) is None
    assert parse_published("", REF) is None
    assert parse_published(None, REF) is None
    assert parse_published("50 оттенков", REF) is None


def test_placeholder_constant():
    assert NO_FRESHNESS == "[НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ОЦЕНКИ СВЕЖЕСТИ]"
