"""Экспорт Markdown-отчёта в DOCX через pandoc из pypandoc-binary."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pypandoc

INPUT_FORMAT = "gfm"


class DocxError(Exception):
    """Понятная пользователю ошибка сборки DOCX."""


def _header(title: str, niche: str, region: str, generated_at: datetime | None) -> str:
    lines = [f"# {title}", ""]
    meta = []
    if niche:
        meta.append(f"**Ниша:** {niche}")
    if region:
        meta.append(f"**Регион:** {region}")
    moment = generated_at or datetime.now()
    meta.append(f"**Дата:** {moment.strftime('%d.%m.%Y %H:%M')}")
    lines.append(" · ".join(meta))
    lines.append("")
    return "\n".join(lines)


def report_to_docx(report_md: str, title: str, niche: str = "", region: str = "",
                   generated_at: datetime | None = None) -> bytes:
    """Собрать DOCX: шапка (ниша, регион, дата) + Markdown-отчёт (gfm → таблицы Word)."""
    md = _header(title, niche, region, generated_at) + report_md.strip() + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.docx"
        try:
            pypandoc.convert_text(md, "docx", format=INPUT_FORMAT, outputfile=str(out))
        except (OSError, RuntimeError) as exc:
            raise DocxError(f"Не удалось собрать DOCX: {exc}") from exc
        return out.read_bytes()
