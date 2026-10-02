"""Дайджест выдачи для LLM: все поля объявлений + метрики + карта спроса + правила."""

from __future__ import annotations

from datetime import datetime

from .dates import NO_FRESHNESS
from .metrics import FRESHNESS_CLASSES

FULL_DESCRIPTION_LIMIT = 50
DESCRIPTION_CUTOFF = 2000


def rules(parsed_at: datetime) -> str:
    return f"""
НАПОМИНАНИЯ ПРАВИЛ АНАЛИЗА (время парсинга выдачи: {parsed_at.strftime('%d.%m.%Y %H:%M')}):
- Интересуют только ДВЕ категории объявлений: СВЕЖИЕ и СТАРЫЕ. Промежуточные сроки
  (сколько именно дней на площадке) значения не имеют.
- «Дата публикации» — дата последней публикации/ПРОДЛЕНИЯ, а не создания. Истинный возраст
  неизвестен (объявление могло продляться годами).
- СВЕЖЕЕ — только по косвенным показателям: «Дата публикации» не старше 7 дней И связка
  «Просмотров сегодня» ↔ «Просмотров всего» согласуется с коротким сроком жизни
  (при сегодняшнем темпе «Всего» набирались бы за дни, а не за месяцы). Если при свежей дате
  «Просмотров всего» слишком много — это СТАРОЕ (продлённое).
- СТАРОЕ — всё, что старше 7 дней по дате, а также продлённые объявления с накопленными
  просмотрами. Код уже проставил класс свежести каждому объявлению — не переоценивай.
- ГЛАВНЫЙ ВОПРОС отчёта: что эффективнее — СВЕЖИЕ или СТАРЫЕ объявления?
  Оценивай эффективность по «Просмотров сегодня» (главный сигнал).
- Мало/нет свежих объявлений — это НЕ «низкая активность». Если старые работают (есть
  «Просмотров сегодня»): старые объявления работают хорошо, спрос закрыт стабильно,
  необходимости каждый день генерить новые объявления нет. «Низкая активность» — только
  когда «Просмотров сегодня» близки к нулю и у свежих, и у старых.
- Кластеры: товарный = конкретный товар (напр. «мох в коробках»); характеристический =
  характеристики (размер, материал); сценарный = сценарий (подарок, озеленение);
  проблемный = проблема («не приживается»); коммерческий = условия (опт, доставка).
- «Просмотров сегодня» — ГЛАВНЫЙ сигнал текущей активности и эффективности. Он привязан
  ко времени парсинга: {parsed_at.strftime('%d.%m.%Y %H:%M')}. Учитывай это (для выгрузки
  вне начала дня «просмотров сегодня» — неполный день).
- «Просмотров всего» — НЕ показатель эффективности: накапливается за всю жизнь объявления,
  включая периоды до последнего продления.
- Если данных нет — [НЕТ ДАННЫХ]; не выдумывай цифры. Нет даты —
  [НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ОЦЕНКИ СВЕЖЕСТИ].
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
        return NO_FRESHNESS
    return (
        f"{record['published_display']} → {record['age_days']} дн. с (пере)публикации → "
        f"класс: {record['freshness_class']} ({record['freshness_basis']})"
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
        f"### Объявление #{record['n']} [{record.get('freshness_class') or NO_FRESHNESS}]",
        f"- Заголовок: {_fmt(record.get('title'))}",
        f"- Просмотры: {_fmt_views(record)}",
        f"- Цена: {_fmt_price(record)}",
        f"- Свежесть: {_fmt_freshness(record)}",
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

    lines.append("### Свежие vs старые (косвенная оценка)")
    lines.append("| Класс | Объявлений |")
    lines.append("|---|---:|")
    for label in FRESHNESS_CLASSES:
        lines.append(f"| {label} | {summary['class_counts'].get(label, 0)} |")
    lines.append(f"| {NO_FRESHNESS} | {summary['class_counts'].get(NO_FRESHNESS, 0)} |")
    lines.append("")

    lines.append("### Эффективность: что эффективнее — свежие или старые?")
    lines.append(
        "Метрика эффективности — «Просмотров сегодня» (на момент парсинга). "
        "«Всего» — только для справки, это накопленная за всю жизнь цифра."
    )
    lines.append("")
    lines.append("| Метрика | свежее | старое |")
    lines.append("|---|---:|---:|")
    rows = [
        ("Объявлений", "count"),
        ("Просмотров сегодня — медиана", "views_today_median"),
        ("Просмотров сегодня — сумма", "views_today_sum"),
        ("Всего просмотров — медиана (справочно)", "views_total_median"),
        ("С платными услугами, %", "promo_share"),
        ("Цена — медиана", "price_median"),
    ]
    stats = summary["fresh_vs_stale"]
    for title, key in rows:
        f = stats.get(FRESHNESS_CLASSES[0], {}).get(key)
        s = stats.get(FRESHNESS_CLASSES[1], {}).get(key)
        lines.append(f"| {title} | {_fmt(f)} | {_fmt(s)} |")
    lines.append("")

    lines.append("### Активность: Просмотров сегодня (главный сигнал, на момент парсинга)")
    if summary.get("views_today_top"):
        lines.append("| № | Заголовок | Класс | Сегодня | Всего |")
        lines.append("|---:|---|---|---:|---:|")
        for item in summary["views_today_top"]:
            lines.append(
                f"| {item['n']} | {_fmt(item['title'])} | {item['freshness_class']} | "
                f"{_fmt(item['views_today'])} | {_fmt(item['views_total'])} |"
            )
    else:
        lines.append(f"{NO_FRESHNESS} для «Просмотров сегодня».")
    lines.append("")

    lines.append("### Цены по классам")
    lines.append("| Класс | N | Мин | Медиана | Макс |")
    lines.append("|---|---:|---:|---:|---:|")
    for label in FRESHNESS_CLASSES:
        s = summary["price_by_class"][label]
        lines.append(
            f"| {label} | {s['count']} | {_fmt(s['min'])} | {_fmt(s['median'])} | {_fmt(s['max'])} |"
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

    lines.append("### Пре-кластеры (категория 1)")
    lines.append("| Категория | Объявлений |")
    lines.append("|---|---:|")
    for name, count in (summary["category_1"] or []):
        lines.append(f"| {name} | {count} |")
    if not summary["category_1"]:
        lines.append(f"| {NO_FRESHNESS} | — |")
    lines.append("")

    lines.append("### Продавцы")
    lines.append("| Продавец | Всего в выдаче | свежее | старое | Рейтинг | Отзывы | Документы |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for seller in summary["sellers"][:20]:
        lines.append(
            f"| {seller['name']} | {seller['listings_in_serp']} | {seller['fresh']} | "
            f"{seller['stale']} | {_fmt(seller['rating'])} | "
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
        "## Карта спроса (счётчики посчитаны кодом; классы свежести — кодовые)",
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
