import io
import zipfile
from datetime import datetime

import pytest

from src.docx_export import DocxError, report_to_docx

REPORT_MD = """# Аналитический отчёт

## 1. Вводные

Мох в горшках — 42 объявления.

| Кластер | свежее | старое |
|---|---:|---:|
| мох | 3 | 11 |

## 9. Решение о входе

**Вердикт:** ВХОДИТЬ.
"""


def _document_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_docx_contains_header_and_report():
    data = report_to_docx(
        REPORT_MD, "Отчёт по нише", niche="мох", region="Москва",
        generated_at=datetime(2026, 10, 14, 12, 0),
    )
    assert data[:2] == b"PK"
    xml = _document_xml(data)
    assert "Отчёт по нише" in xml
    assert "мох" in xml
    assert "Москва" in xml
    assert "14.10.2026" in xml
    assert "42 объявления" in xml
    assert "Вердикт" in xml


def test_docx_converts_markdown_table_to_word_table():
    data = report_to_docx(REPORT_MD, "Отчёт")
    xml = _document_xml(data)
    assert "<w:tbl>" in xml


def test_docx_without_meta_still_builds():
    data = report_to_docx("просто текст", "Т")
    assert data[:2] == b"PK"
    assert "просто текст" in _document_xml(data)


def test_docx_error_on_pandoc_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("pandoc not found")

    monkeypatch.setattr("src.docx_export.pypandoc.convert_text", boom)
    with pytest.raises(DocxError) as exc:
        report_to_docx("текст", "Т")
    assert "Не удалось собрать DOCX" in str(exc.value)
