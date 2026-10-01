import hashlib
import io
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st

from src import config
from src.digest import build_digest
from src.llm import LLMError, build_system_prompt, classify_listings, generate_report
from src.metrics import build_summary, compute_listing_metrics
from src.parse_excel import parse_workbook
from src.report import assemble_report, build_demand_map_table, report_filename

st.set_page_config(page_title="Avito SERP Analyzer", page_icon="📊", layout="wide")

PROMPT_PATH = config.BASE_DIR / "prompts" / "analyst.md"
MAX_UPLOAD_MB = 20

PREVIEW_COLUMNS = {
    "n": "#",
    "title": "Заголовок",
    "price_raw": "Цена",
    "published_display": "Дата публикации",
    "freshness_bucket": "Бакет свежести",
    "views_total": "Всего просмотров",
    "views_per_day": "Просм./день",
    "position": "Позиция",
    "seller_name": "Продавец",
    "paid_services": "Платные услуги",
}


def _file_hash(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _reference_now(export_date) -> datetime:
    if export_date is None:
        return datetime.now()
    if isinstance(export_date, datetime):
        export_date = export_date.date()
    today = datetime.now().date()
    chosen = min(export_date, today)
    return datetime.combine(chosen, time(12, 0))


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Не найден промт: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _render_report(result: dict):
    st.success("Отчёт готов.")
    st.markdown(result["report"])
    st.download_button(
        "Скачать отчёт (.md)",
        data=result["report"],
        file_name=result["filename"],
        mime="text/markdown",
        use_container_width=True,
    )


with st.sidebar:
    st.title("📊 Avito Analyzer")
    api_key = config.get_api_key()
    st.caption(f"API key: {config.mask_key(api_key)}")
    st.caption(f"Base URL: {config.get_base_url()}")
    st.divider()
    niche = st.text_input("Ниша / товар", placeholder="например: кактусы в горшках")
    region = st.text_input("Регион", placeholder="например: Москва")
    with st.expander("Дополнительно"):
        export_date = st.date_input(
            "Дата выгрузки (reference-now)",
            value=None,
            help="Если не указана — берётся текущие дата и время.",
        )
        model_override = st.text_input(
            "Модель LLM",
            value="",
            placeholder=config.DEFAULT_MODEL,
            help="Пусто — значение из LLM_MODEL",
        )
    if not api_key:
        st.warning("Не задан ROUTERAI_API_KEY — анализ не запустится.")

st.title("Анализ поисковой выдачи Avito")
st.caption(
    "Excel-выгрузка → метрики и бакеты свежести (код) → кластеризация спроса (LLM) "
    "→ Markdown-отчёт с секциями 1–10."
)

uploaded = st.file_uploader("Excel-файл выгрузки (.xlsx)", type=["xlsx", "xlsm"])
if uploaded is None:
    st.info("Загрузите Excel-файл выгрузки, заполните нишу и регион и нажмите «Анализ».")
    st.stop()

file_bytes = uploaded.getvalue()
if len(file_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
    st.error(f"Файл больше {MAX_UPLOAD_MB} МБ — уменьшите выгрузку.")
    st.stop()

file_hash = _file_hash(file_bytes)
parse_key = f"parsed_{file_hash}"

if parse_key not in st.session_state:
    try:
        st.session_state[parse_key] = parse_workbook(io.BytesIO(file_bytes))
    except Exception as exc:
        st.error(f"Не удалось прочитать Excel: {exc}")
        st.stop()

parsed = st.session_state[parse_key]
records = parsed["records"]

st.subheader("Нормализованные данные")
st.caption(
    f"Строк: {len(records)} · Колонок в файле: {parsed['total_columns']} · "
    f"Проигнорировано товарных колонок: {len(parsed['ignored'])}"
)
for warning in parsed["warnings"]:
    st.warning(warning)

if records:
    reference_now = _reference_now(export_date)
    preview_records = [compute_listing_metrics(r, reference_now) for r in records]
    preview_df = pd.DataFrame(preview_records)
    st.dataframe(
        preview_df[list(PREVIEW_COLUMNS)].rename(columns=PREVIEW_COLUMNS),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

run = st.button("Анализ", type="primary", disabled=not (niche.strip() and region.strip() and api_key))
if not (niche.strip() and region.strip()):
    st.caption("Укажите нишу и регион в боковой панели.")
elif not api_key:
    st.caption("Задайте ROUTERAI_API_KEY для запуска анализа.")

result_key = "|".join([
    file_hash, niche.strip(), region.strip(), config.get_model(model_override),
    str(export_date or "now"),
])
results: dict = st.session_state.setdefault("results", {})
stage_a_cache: dict = st.session_state.setdefault("stage_a_cache", {})

if run or (result_key in results):
    if result_key in results:
        _render_report(results[result_key])
    else:
        reference_now = _reference_now(export_date)
        progress = st.progress(0.0, text="Парсинг и метрики…")
        status = st.empty()
        stage_key = None

        try:
            listings = [compute_listing_metrics(r, reference_now) for r in records]
            summary = build_summary(listings)
            progress.progress(0.15, text="Метрики посчитаны")
            status.caption(
                "Бакеты: " + ", ".join(f"{k}: {v}" for k, v in summary["bucket_counts"].items())
            )

            status.info("Стадия A — кластеризация объявлений (LLM)…")
            stage_key = f"{file_hash}|{config.get_model(model_override)}"
            if stage_key in stage_a_cache:
                classifications = stage_a_cache[stage_key]
                progress.progress(0.55, text="Стадия A — из кэша")
            else:
                def stage_a_progress(done: int, total: int):
                    progress.progress(0.15 + 0.4 * done / max(total, 1),
                                      text=f"Стадия A: чанк {done}/{total}")

                classifications, stage_a_error = classify_listings(
                    listings, model=model_override, progress=stage_a_progress
                )
                if classifications is not None:
                    stage_a_cache[stage_key] = classifications
                    progress.progress(0.55, text="Стадия A завершена")
                else:
                    progress.progress(0.55, text="Стадия A — JSON не получен, карта спроса с [НЕТ ДАННЫХ]")
                    status.warning(
                        f"Стадия A не вернула валидный JSON: {stage_a_error}. "
                        "Карта спроса будет с [НЕТ ДАННЫХ], отчёт продолжит строиться."
                    )

            demand_map_md = build_demand_map_table(classifications, listings)
            digest_text = build_digest(listings, summary, demand_map_md,
                                       niche.strip(), region.strip(), reference_now)

            status.info("Стадия B — генерация отчёта (LLM)…")
            system_prompt = build_system_prompt(
                niche.strip(), region.strip(), _load_prompt()
            )
            stage_b_md = generate_report(system_prompt, digest_text, model=model_override)
            progress.progress(0.9, text="Сборка отчёта…")

            report_md = assemble_report(
                stage_b_md, demand_map_md,
                niche=niche.strip(), region=region.strip(),
                total=len(listings), model=config.get_model(model_override),
            )
            results[result_key] = {
                "report": report_md,
                "filename": report_filename(niche.strip()),
            }
            progress.progress(1.0, text="Готово")
            status.empty()
            _render_report(results[result_key])

        except LLMError as exc:
            progress.empty()
            status.empty()
            st.error(str(exc))
            if st.button("Повторить"):
                if stage_key:
                    stage_a_cache.pop(stage_key, None)
                st.rerun()
        except FileNotFoundError as exc:
            progress.empty()
            status.empty()
            st.error(str(exc))
        except Exception as exc:
            progress.empty()
            status.empty()
            st.error(f"Ошибка анализа: {exc}")
