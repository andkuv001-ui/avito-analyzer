"""Fuzzy-маппинг ядерных колонок Avito-выгрузки по русским заголовкам."""

from __future__ import annotations

import re

CORE_COLUMNS: dict[str, str] = {
    "title": "Заголовок",
    "price": "Цена",
    "description": "Описание",
    "url": "Ссылка на объявление",
    "avito_id": "Avito ID",
    "published_at": "Дата публикации",
    "position": "Позиция объявлений",
    "views_today": "Просмотров сегодня",
    "views_total": "Всего просмотров",
    "paid_services": "Платные услуги",
    "address": "Адрес",
    "seller_name": "Название продавца",
    "seller_url": "Ссылка на продавца",
    "docs_verified": "Документы проверены",
    "seller_listings": "Кол-во объявлений",
    "rating": "Рейтинг",
    "reviews": "Отзывы",
}

for _i in range(1, 7):
    CORE_COLUMNS[f"category_{_i}"] = f"категория {_i}"

_WS_RE = re.compile(r"\s+")


def normalize_header(name: str) -> str:
    return _WS_RE.sub(" ", str(name).strip().lower().replace("ё", "е")).strip('"\'«»')


def map_columns(columns) -> dict[str, str]:
    """canonical_key -> оригинальное имя колонки в файле."""
    normalized = [(col, normalize_header(col)) for col in columns]
    mapping: dict[str, str] = {}

    for key, expected in CORE_COLUMNS.items():
        exp = normalize_header(expected)
        for orig, norm in normalized:
            if norm == exp:
                mapping[key] = orig
                break

    for key, expected in CORE_COLUMNS.items():
        if key in mapping:
            continue
        exp = normalize_header(expected)
        candidates = [
            orig for orig, norm in normalized
            if orig not in mapping.values()
            and (norm.startswith(exp) or exp.startswith(norm))
            and min(len(norm), len(exp)) >= 4
        ]
        if len(candidates) == 1:
            mapping[key] = candidates[0]

    return mapping


def ignored_columns(columns, mapping: dict[str, str]) -> list[str]:
    mapped = set(mapping.values())
    return [col for col in columns if col not in mapped]


def missing_core_warnings(mapping: dict[str, str]) -> list[str]:
    return [
        f"Нет колонки «{expected}» — значения будут [{_PLACEHOLDER}]"
        for key, expected in CORE_COLUMNS.items()
        if key not in mapping
    ]


_PLACEHOLDER = "НЕТ ДАННЫХ"
