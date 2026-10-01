from src.columns import CORE_COLUMNS, ignored_columns, map_columns, missing_core_warnings

USER_COLUMNS = [
    "Заголовок", "Цена", "Описание", "Ссылка на объявление", "Avito ID",
    "Дата публикации", "Позиция объявлений", "Просмотров сегодня", "Всего просмотров",
    "Платные услуги", "Адрес", "Название продавца", "Ссылка на продавца",
    "Документы проверены", "Кол-во объявлений", "Рейтинг", "Отзывы",
    "категория 1", "категория 2", "категория 3", "категория 4", "категория 5", "категория 6",
    "Высота", "Материал", "Тип горшка",
]

MERCHANT_COLUMNS = ["Высота", "Материал", "Тип горшка"]


def test_maps_all_core_columns():
    mapping = map_columns(USER_COLUMNS)
    assert set(mapping) == set(CORE_COLUMNS)
    assert mapping["title"] == "Заголовок"
    assert mapping["published_at"] == "Дата публикации"
    assert mapping["views_total"] == "Всего просмотров"
    assert mapping["category_6"] == "категория 6"


def test_case_yo_and_spacing_fuzzy():
    columns = ["  заголовок ", "ЦЕНА", "дата  публикации", "Всего   просмотров"]
    mapping = map_columns(columns)
    assert mapping["title"] == "  заголовок "
    assert mapping["price"] == "ЦЕНА"
    assert mapping["published_at"] == "дата  публикации"
    assert mapping["views_total"] == "Всего   просмотров"


def test_merchant_columns_ignored():
    mapping = map_columns(USER_COLUMNS)
    ignored = ignored_columns(USER_COLUMNS, mapping)
    assert set(ignored) == set(MERCHANT_COLUMNS)
    assert len(ignored) == 3


def test_missing_core_produces_warning():
    mapping = map_columns(["Заголовок", "Цена"])
    warnings = missing_core_warnings(mapping)
    assert any("Дата публикации" in w for w in warnings)
    assert any("[НЕТ ДАННЫХ]" in w for w in warnings)
    assert not any("Заголовок" in w for w in warnings)


def test_extra_niche_column_ignored():
    columns = USER_COLUMNS + ["Цвет цветка", "Диаметр"]
    mapping = map_columns(columns)
    ignored = ignored_columns(columns, mapping)
    assert "Цвет цветка" in ignored and "Диаметр" in ignored
