"""Дайджест выдачи для LLM: все поля объявлений + метрики + карта спроса + правила."""

from __future__ import annotations

from datetime import datetime

from .dates import NO_FRESHNESS
from .metrics import (
    FRESHNESS_CLASSES,
    NO_DATA,
    SEGMENT_LABELS,
    _fmt_int,
    _ratio,
    build_fact_pack,
    cluster_price_stats,
)

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
- ГЛАВНЫЙ ВОПРОС отчёта — решение о входе в нишу: обзор факторов входа. Сравнение
  свежих/старых — сигнал «как ведут себя игроки» (секция активности); эффективность
  оценивай по «Просмотров сегодня».
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
- ЦЕНА — ГЛАВНЫЙ КОНФАУНДЕР «Просмотров сегодня»: дешёвые объявления собирают больше
  просмотров ВСЕГДА. Любые сравнения просмотров (свежие vs старые, promo, кластеры) —
  только ВНУТРИ ценовых сегментов (дешёвые/средние/дорогие): границы тертилей и разрезы
  даны кодом в таблицах «Сегменты цены» и «Свежие vs старые внутри ценовых сегментов».
  Преимущество свежих/старых или promo, объяснённое только ценой, — НЕ преимущество
  формата или продвижения.
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


def _format_record(record: dict, cutoff: int | None,
                   classifications: dict[int, dict] | None = None) -> str:
    description = record.get("description") or "[НЕТ ДАННЫХ]"
    if cutoff is not None and len(description) > cutoff:
        description = description[:cutoff] + "… [ОПИСАНИЕ ОБРЕЗАНО]"

    cls = (classifications or {}).get(record["n"])
    if cls is None:
        queries = usp = scenario = b2b = "[НЕТ ДАННЫХ]"
    else:
        queries = ", ".join(str(q) for q in cls.get("queries") or []) or "[НЕТ ДАННЫХ]"
        usp = _fmt(cls.get("usp"))
        scenario = _fmt(cls.get("scenario"))
        b2b = _fmt(cls.get("b2b"))

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
        f"- Запросы: {queries}",
        f"- УТП: {usp}",
        f"- Сценарий: {scenario}",
        f"- Тип: {b2b}",
        f"- Ссылка: {_fmt(record.get('url'))}",
        f"- Avito ID: {_fmt(record.get('avito_id'))}",
        f"- Описание: {description}",
    ])


def build_metrics_tables(summary: dict, listings: list[dict] | None = None,
                         classifications: dict[int, dict] | None = None) -> str:
    lines = ["## Сводные метрики (считаны кодом, не LLM)", ""]

    lines.append("### Свежие vs старые (косвенная оценка)")
    lines.append("| Класс | Объявлений |")
    lines.append("|---|---:|")
    for label in FRESHNESS_CLASSES:
        lines.append(f"| {label} | {summary['class_counts'].get(label, 0)} |")
    lines.append(f"| {NO_FRESHNESS} | {summary['class_counts'].get(NO_FRESHNESS, 0)} |")
    lines.append("")

    lines.append("### Эффективность свежих vs старых (без учёта цены)")
    lines.append(
        "Метрика эффективности — «Просмотров сегодня» (на момент парсинга). "
        "«Всего» — только для справки, это накопленная за всю жизнь цифра. "
        "Сравнение по всей выдаче конфаундится ценой — см. разрезы по сегментам."
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

    segments = summary["price_segments"]
    lines.append("### Сегменты цены: дешёвые / средние / дорогие")
    if segments["границы"] is None:
        lines.append(
            f"Сегменты не считаются: с числовой ценой {segments['база']} "
            f"(<9); без числовой цены — {segments['без цены']}."
        )
    else:
        p33, p66 = segments["границы"]
        lines.append(
            f"Тертили по числовой цене: дешёвые ≤ {_fmt_int(round(p33))} < средние "
            f"≤ {_fmt_int(round(p66))} < дорогие; без числовой цены (в т.ч. «договорная») "
            f"— {segments['без цены']}."
        )
    lines.append("| Сегмент | Объявлений | Цена min–med–max | «Просмотров сегодня» медиана "
                 "| С платными услугами, % |")
    lines.append("|---|---:|---:|---:|---:|")
    for label in SEGMENT_LABELS:
        seg = segments["segments"][label]
        if seg["count"] is None:
            lines.append(f"| {label} | {NO_DATA} | {NO_DATA} | {NO_DATA} | {NO_DATA} |")
            continue
        price_range = (
            f"{_fmt_int(seg['price_min'])}–{_fmt_int(seg['price_median'])}–"
            f"{_fmt_int(seg['price_max'])}"
            if seg["price_min"] is not None else NO_DATA
        )
        lines.append(
            f"| {label} | {seg['count']} | {price_range} | "
            f"{_fmt(seg['views_today_median'])} | {_fmt(seg['promo_share'])} |"
        )
    lines.append("")

    lines.append("### Свежие vs старые внутри ценовых сегментов")
    lines.append(
        "Контроль цены: цена — конфаундер просмотров, поэтому сравнение свежих и старых "
        "даётся внутри каждого ценового сегмента."
    )
    lines.append("")
    lines.append("| Сегмент | N свежих | Просмотров сегодня медиана (свежие) | N старых "
                 "| Просмотров сегодня медиана (старые) | Отношение |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label in SEGMENT_LABELS:
        seg = segments["segments"][label]
        if seg["count"] is None:
            lines.append(f"| {label} | {NO_DATA} | {NO_DATA} | {NO_DATA} | {NO_DATA} | "
                         f"{NO_DATA} |")
            continue
        fresh, stale = seg["fresh"], seg["stale"]
        lines.append(
            f"| {label} | {fresh['count']} | {_fmt(fresh['views_today_median'])} | "
            f"{stale['count']} | {_fmt(stale['views_today_median'])} | "
            f"{_ratio(fresh['views_today_median'], stale['views_today_median'])} |"
        )
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

    lines.append("### Цены по кластерам")
    cluster_rows = cluster_price_stats(listings or [], classifications)
    if cluster_rows:
        lines.append("| Тег | Тип | N | Медиана цены | «Просмотров сегодня» медиана |")
        lines.append("|---|---|---:|---:|---:|")
        for row in cluster_rows:
            price_cell = _fmt_int(row["price_median"]) if row["price_median"] is not None \
                else NO_DATA
            lines.append(
                f"| {row['tag']} | {row['kind'] or '—'} | {row['count']} | "
                f"{price_cell} | {_fmt(row['views_today_median'])} |"
            )
    else:
        lines.append(f"| {NO_DATA} | — | — | — | — |")
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
                 parsed_at: datetime | None = None,
                 classifications: dict[int, dict] | None = None) -> str:
    parsed_at = parsed_at or reference_now
    cutoff = None if len(listings) <= FULL_DESCRIPTION_LIMIT else DESCRIPTION_CUTOFF
    facts = build_fact_pack(listings, summary, classifications)

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
        "## Ключевые факты (посчитаны кодом)",
        "",
        *facts,
        "",
        build_metrics_tables(summary, listings, classifications),
        "## Карта спроса (счётчики посчитаны кодом; классы свежести — кодовые)",
        "",
        demand_map_md,
        "",
        "## Полный дайджест объявлений",
        "",
    ]
    for record in listings:
        parts.append(_format_record(record, cutoff, classifications))
        parts.append("")
    return "\n".join(parts)
