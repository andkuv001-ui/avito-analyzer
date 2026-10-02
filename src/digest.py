"""Дайджест выдачи для LLM: все поля объявлений + метрики + карта спроса + правила."""

from __future__ import annotations

from datetime import datetime

from .dates import NO_FRESHNESS
from .metrics import BUCKET_LABELS, FRESHNESS_BUCKETS

FULL_DESCRIPTION_LIMIT = 50
DESCRIPTION_CUTOFF = 2000


def rules(parsed_at: datetime) -> str:
    return f"""
НАПОМИНАНИЯ ПРАВИЛ АНАЛИЗА (время парсинга выдачи: {parsed_at.strftime('%d.%m.%Y %H:%M')}):
- «Просмотров сегодня» — ГЛАВНЫЙ сигнал текущей активности и эффективности объявления.
  Он привязан ко времени парсинга: выгрузка сделана {parsed_at.strftime('%d.%m.%Y в %H:%M')},
  учитывай это (для выгрузки вне утра «просмотров сегодня» — неполный день).
- «Просмотров всего» — НЕ показатель эффективности: цифра накапливается за всю жизнь
  объявления, включая периоды до последнего продления.
- «Дата публикации» в выгрузке — дата последней публикации/ПРОДЛЕНИЯ, а не создания.
  Истинный возраст объявления неизвестен (могло продляться ежемесячно годами).
  Бакеты свежести 0–1/2–3/4–7/8–30/31+ означают «дней с (пере)публикации», не возраст.
- Свежесть определяй только КОСВЕННО: «Дата публикации» ≤ 7 дней И соотношение
  «Просмотров сегодня» к «Просмотров всего» согласуется с коротким сроком жизни
  (сегодняшние просмотры — заметная доля от общих). Если при свежей дате «Просмотров всего»
  много — это продлённое старое объявление.
- Если данных нет — ставь [НЕТ ДАННЫХ]; возраст и просмотры не выдумывай.
- Нет даты: [НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ОЦЕНКИ СВЕЖЕСТИ].
- Порядок рассуждения: свежесть (косвенно) → структура спроса → конкуренция →
  коммерческие сценарии → гипотезы.
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
        f"{record['published_display']} → {record['age_days']} дн. с (пере)публикации, "
        f"бакет {record['freshness_bucket']} (истинный возраст неизвестен)"
    )


def _fmt_views(record: dict) -> str:
    parts = [
        f"сегодня: {_fmt(record.get('views_today'))} (на момент парсинга)",
        f"всего: {_fmt(record.get('views_total'))} (накоплено за всю жизнь, вкл. периоды до продления)",
    ]
    share = record.get("views_today_share")
    if share is not None:
        parts.append(f"сегодняшние — {share}% от общих")
    return ", ".join(parts)


def _format_record(record: dict, cutoff: int | None) -> str:
    description = record.get("description") or "[НЕТ ДАННЫХ]"
    if cutoff is not None and len(description) > cutoff:
        description = description[:cutoff] + "… [ОПИСАНИЕ ОБРЕЗАНО]"

    return "\n".join([
        f"### Объявление #{record['n']}",
        f"- Заголовок: {_fmt(record.get('title'))}",
        f"- Просмотры: {_fmt_views(record)}",
        f"- Цена: {_fmt_price(record)}",
        f"- Дата публикации/продления: {_fmt_freshness(record)}",
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

    lines.append("### Активность: Просмотров сегодня (главный сигнал, на момент парсинга)")
    if summary.get("views_today_top"):
        lines.append("| № | Заголовок | Сегодня | Всего | Доля сегодняшних |")
        lines.append("|---:|---|---:|---:|---:|")
        for item in summary["views_today_top"]:
            lines.append(
                f"| {item['n']} | {_fmt(item['title'])} | {_fmt(item['views_today'])} | "
                f"{_fmt(item['views_total'])} | см. данные объявления |"
            )
    else:
        lines.append(f"{NO_FRESHNESS} для «Просмотров сегодня».")
    lines.append("")

    lines.append("### Дней с (пере)публикации (бакеты; истинный возраст неизвестен)")
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
        lines.append("[НЕТ ДАННЫХ] — платные услуги не указаны ни в одном объявлении.")
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
                 niche: str, region: str, reference_now: datetime,
                 parsed_at: datetime | None = None) -> str:
    parsed_at = parsed_at or reference_now
    cutoff = None if len(listings) <= FULL_DESCRIPTION_LIMIT else DESCRIPTION_CUTOFF

    parts = [
        "# ДАЙДЖЕСТ ВЫДАЧИ AVITO",
        f"Ниша/товар: {niche}",
        f"Регион: {region}",
        f"Время ПАРСИНГА выгрузки: {parsed_at.strftime('%d.%m.%Y %H:%M')} "
        f"(«Просмотров сегодня» — на этот момент)",
        f"Дата анализа: {reference_now.strftime('%d.%m.%Y %H:%M')}",
        f"Объявлений в выдаче: {len(listings)}",
        f"Описания: {'целиком' if cutoff is None else f'обрезаны до {DESCRIPTION_CUTOFF} символов'}",
        "",
        rules(parsed_at),
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
