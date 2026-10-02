"""Сборка финального Markdown-отчёта и карта спроса из кодовых счётчиков."""

from __future__ import annotations

import re
from datetime import datetime

from .dates import NO_FRESHNESS
from .metrics import BUCKET_LABELS, FRESHNESS_BUCKETS

DEMAND_MAP_PLACEHOLDER = "[ТАБЛИЦА КАРТЫ СПРОСА]"
NO_DATA_LABEL = "[НЕТ ДАННЫХ]"


def build_demand_map_table(classifications: dict[int, dict] | None, listings: list[dict]) -> str:
    """Точная карта спроса: строки — кластеры из стадии A, столбцы — бакеты свежести из кода."""
    columns = BUCKET_LABELS + [NO_FRESHNESS]
    counts: dict[tuple[str, str], int] = {}
    row_totals: dict[tuple[str, str], int] = {}

    def bump(row_key: tuple[str, str], bucket: str):
        counts[(row_key, bucket)] = counts.get((row_key, bucket), 0) + 1
        row_totals[row_key] = row_totals.get(row_key, 0) + 1

    if classifications is None:
        for record in listings:
            bump((NO_DATA_LABEL, ""), record.get("freshness_bucket") or NO_FRESHNESS)
    else:
        for record in listings:
            bucket = record.get("freshness_bucket") or NO_FRESHNESS
            clusters = (classifications.get(record["n"]) or {}).get("clusters") or []
            if not clusters:
                bump(("(без тегов)", ""), bucket)
                continue
            for cluster in clusters:
                bump((cluster["tag"].strip(), cluster["kind"]), bucket)

    if not counts:
        return f"*{NO_DATA_LABEL}*"

    header = "| Кластер | Тип | " + " | ".join(f"{c} дней" if c != NO_FRESHNESS else c
                                          for c in columns) + " | ИТОГО |"
    sep = "|---|---|" + "---:|" * (len(columns) + 1)
    rows = [header, sep]

    order = sorted(row_totals, key=lambda k: (-row_totals[k], k[0]))
    for key in order:
        tag, kind = key
        cells = [str(counts.get((key, col), 0)) for col in columns]
        rows.append(f"| {tag} | {kind or '—'} | " + " | ".join(cells) + f" | {row_totals[key]} |")

    col_totals = [sum(counts.get((k, col), 0) for k in row_totals) for col in columns]
    rows.append("| **ИТОГО** | — | " + " | ".join(f"**{t}**" for t in col_totals)
                + f" | **{sum(row_totals.values())}** |")
    return "\n".join(rows)


def _insert_demand_map(stage_b_md: str, table_md: str) -> str:
    if DEMAND_MAP_PLACEHOLDER in stage_b_md:
        return stage_b_md.replace(DEMAND_MAP_PLACEHOLDER, table_md, 1)

    match = re.search(r"^(#{2,3}\s*8[\.:].*)$", stage_b_md, flags=re.MULTILINE)
    if match:
        insert_at = match.end()
        return stage_b_md[:insert_at] + "\n\n" + table_md + stage_b_md[insert_at:]

    return stage_b_md.rstrip() + f"\n\n## 8. Карта спроса\n\n{table_md}\n"


def meta_block(niche: str, region: str, total: int, model: str,
               generated_at: datetime, parsed_at: datetime | None = None) -> str:
    lines = [
        f"- **Ниша / товар:** {niche}",
        f"- **Регион:** {region}",
        f"- **Объявлений в выдаче:** {total}",
    ]
    if parsed_at is not None:
        lines.append(
            f"- **Время парсинга выгрузки:** {parsed_at.strftime('%d.%m.%Y %H:%M')} "
            f"(«Просмотров сегодня» — на этот момент)"
        )
    lines += [
        f"- **Модель:** {model}",
        f"- **Сформирован:** {generated_at.strftime('%d.%m.%Y %H:%M')}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def assemble_report(stage_b_md: str, demand_map_md: str, niche: str, region: str,
                    total: int, model: str, generated_at: datetime | None = None,
                    parsed_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    body = _insert_demand_map(stage_b_md.strip(), demand_map_md)
    body = re.sub(r"^#\s+Аналитический отчёт[^\n]*\n+", "", body, count=1)
    parts = ["# Аналитический отчёт по выдаче Avito", "",
             meta_block(niche, region, total, model, generated_at, parsed_at), body, ""]
    return "\n".join(parts)


def report_filename(niche: str, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    slug = re.sub(r"[^\w\-]+", "-", niche.strip().lower()).strip("-") or "report"
    slug = re.sub(r"-{2,}", "-", slug)[:40]
    return f"avito_report_{slug}_{generated_at.strftime('%Y%m%d-%H%M')}.md"


def demand_map_full(listings: list[dict], classifications: dict[int, dict] | None) -> str:
    """Карта спроса с заголовком секции — для вставки в дайджест и в отчёт."""
    return build_demand_map_table(classifications, listings)


def bucket_summary_line(bucket_counts: dict) -> str:
    parts = [f"{label}: {bucket_counts.get(label, 0)}" for label, _, _ in FRESHNESS_BUCKETS]
    parts.append(f"{NO_FRESHNESS}: {bucket_counts.get(NO_FRESHNESS, 0)}")
    return "; ".join(parts)
