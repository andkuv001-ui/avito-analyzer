"""Дайджест выдачи для LLM: все поля объявлений + метрики + карта спроса + правила."""

from __future__ import annotations

from datetime import datetime

from .dates import NO_FRESHNESS
from .metrics import BUCKET_LABELS, FRESHNESS_BUCKETS

FULL_DESCRIPTION_LIMIT = 50
DESCRIPTION_CUTOFF = 2000

RULES = """
НАПОМИНАНИЯ ПРАВИЛ АНАЛИЗА:
- «Просмотров всего» — НЕ показатель эффективности. Сравнение возможно только с поправкой
  на возраст объявления через views/день, и то с оговоркой: накопленный период неизвестен.
- Свежие объявления (0–3 дня) получают повышенный начальный трафик — прямое сравнение
  со старыми запрещено.
- Долгоживущие объявления (31+ дней): возраст + накопленные просмотры + платные услуги +
  присутствие в выдаче = «потенциально поддерживаемое», а не «эффективное».
- «Просмотров сегодня» — сигнал текущей активности, осмыслен только при свежей выгрузке.
- Если даты нет: возраст не выдумывать, используй метку [НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ОЦЕНКИ СВЕЖЕСТИ].
- Если данных не хватает для карта спроса — ставь [НЕТ ДАННЫХ].
- Порядок рассуждения: свежесть → структура спроса → конкуренция → коммерческие сценарии → гипотезы.
""".strip()


def _fmt(value) -> str:
    if value is None or value == "":
        return "[НЕТ ДАННЫХ]"
    return str(value)


def _fmt_price(record: dict) -> str:
    if record.get("price") is not None:
        text = f"{record['price']:,.0f}".replace(",", " ")
        flags = []
        if record.get("price_range"):
            flags.append("диапазон")
        if record.get("price_negotiable"):
            flags.append("договорная")
        if flags:
            text += f" ({', '.join(flags)})"
        return text
    if record.get("price_raw"):
        return str(record["price_raw"])
    if record.get("price_negotiable"):
        return "договорная"
    return "[НЕТ ДАННЫХ]"


def _fmt_freshness(record: dict) -> str:
    if record.get("published_at") is None:
        return f"{NO_FRESHNESS}"
    return (
        f"{record['published_display']} → возраст {record['age_days']} дн., "
        f"бакет {record['freshness_bucket']}"
    )


def _fmt_views(record: dict) -> str:
    total = record.get("views_total")
    parts = [f"всего: {_fmt(total)}"]
    parts.append(f"сегодня: {_fmt(record.get('views_today'))}")
    vpd = record.get("views_per_day")
    if vpd is not None:
        parts.append(f"~{vpd}/день (накопленный период неизвестен)")
    return ", ".join(parts)


def _format_record(record: dict, cutoff: int | None) -> str:
    description = record.get("description") or "[НЕТ ДАННЫХ]"
    if cutoff is not None and len(description) > cutoff:
        description = description[:cutoff] + "… [ОПИСАНИЕ ОБРЕЗАНО]"

    return "\n".join([
        f"### Объявление #{record['n']}",
        f"- Заголовок: {_fmt(record.get('title'))}",
        f"- Цена: {_fmt_price(record)}",
        f"- Дата публикации: {_fmt_freshness(record)}",
        f"- Просмотры: {_fmt_views(record)}",
        f"- Позиция: {_fmt(record.get('position'))}",
        f"- Платные услуги: {_fmt(record.get('paid_services'))}",
        f"- Продавец: {_fmt(record.get('seller_name'))} (рейтинг {_fmt(record.get('rating'))}, "
        f"отзывы {_fmt(record.get('reviews'))}, объявлений у продавца {_fmt(record.get('seller_listings'))}, "
        f"документы проверены: {'да' if record.get('docs_verified') else 'нет'})",
        f"- Адрес: {_fmt(record.get('address'))}",
        f"- Категории: {' / '.join(record['categories']) if record.get('categories') else '[НЕТ ДАННЫХ]'}",
        f"- Ссылка: {_fmt(record.get('url'))}",
        f"- Avito ID: {_fmt(record.get('avito_id'))}",
        f"- Описание: {description}",
    ])


def build_metrics_tables(summary: dict) -> str:
    lines = ["## Сводные метрики (считаны кодом, не LLM)", ""]

    lines.append("### Возраст выдачи (бакеты)")
    lines.append("| Бакет | Объявлений |")
    lines.append("|---|---:|")
    for label, lo, hi in FRESHNESS_BUCKETS:
        lines.append(f"| {label} дней | {summary['bucket_counts'].get(label, 0)} |")
    lines.append(f"| {NO_FRESHNESS} | {summary['bucket_counts'].get(NO_FRESHNESS, 0)} |")
    lines.append("")

    lines.append("### Цены по бакетам")
    lines.append("| Бакет | N | Мин | Медиана | Макс |")
    lines.append("|---|---:|---:|---:|---:|")
    for label in BUCKET_LABELS:
        stats = summary["price_by_bucket"][label]
        lines.append(
            f"| {label} | {stats['count']} | {_fmt(stats['min'])} | "
            f"{_fmt(stats['median'])} | {_fmt(stats['max'])} |"
        )
    lines.append("")
    overall = summary["price_overall"]
    lines.append(
        f"Всего с числовой ценой: {overall['count']}; договорная: {summary['price_negotiable']}; "
        f"диапазон: {summary['price_range']}."
    )
    lines.append("")

    lines.append("### Продвижение (Платные услуги)")
    if summary["promo_top"]:
        lines.append("| Услуга | Объявлений |")
        lines.append("|---|---:|")
        for service, count in summary["promo_top"]:
            lines.append(f"| {service} | {count} |")
    else:
        lines.append(f"[НЕТ ДАННЫХ] — платные услуги не указаны ни в одном объявлении.")
    lines.append(f"Объявлений с платными услугами: {summary['promo_used']} из {summary['total']}.")
    lines.append("")

    lines.append("### Пре-кластеры (категория 1 / категория 2)")
    lines.append("| Категория | Объявлений |")
    lines.append("|---|---:|")
    for name, count in (summary["category_1"] or []):
        lines.append(f"| {name} | {count} |")
    if not summary["category_1"]:
        lines.append(f"| {NO_FRESHNESS} | — |")
    lines.append("")

    lines.append("### Продавцы")
    lines.append("| Продавец | Объявлений в выдаче | Рейтинг | Отзывы | Документы |")
    lines.append("|---|---:|---:|---:|---|")
    for seller in summary["sellers"][:20]:
        lines.append(
            f"| {seller['name']} | {seller['listings_in_serp']} | {_fmt(seller['rating'])} | "
            f"{_fmt(seller['reviews'])} | {'да' if seller['docs_verified'] else 'нет'} |"
        )
    lines.append("")
    lines.append(f"Всего продавцов в выдаче: {summary['unique_sellers']}.")
    lines.append("")

    return "\n".join(lines)


def build_digest(listings: list[dict], summary: dict, demand_map_md: str,
                 niche: str, region: str, reference_now: datetime) -> str:
    cutoff = None if len(listings) <= FULL_DESCRIPTION_LIMIT else DESCRIPTION_CUTOFF

    parts = [
        f"# ДАЙДЖЕСТ ВЫДАЧИ AVITO",
        f"Ниша/товар: {niche}",
        f"Регион: {region}",
        f"Дата анализа: {reference_now.strftime('%d.%m.%Y %H:%M')}",
        f"Объявлений в выдаче: {len(listings)}",
        f"Описания: {'целиком' if cutoff is None else f'обрезаны до {DESCRIPTION_CUTOFF} символов'}",
        "",
        RULES,
        "",
        build_metrics_tables(summary),
        "## Карта спроса (счётчики посчитаны кодом)",
        "",
        demand_map_md,
        "",
        "## Полный дайджест объявлений",
        "",
    ]
    for record in listings:
        parts.append(_format_record(record, cutoff))
        parts.append("")
    return "\n".join(parts)
