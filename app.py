import hashlib
import io
from datetime import datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

from src import config
from src import session as sess
from src.digest import build_digest
from src.docx_export import DocxError, report_to_docx
from src.llm import LLMError, build_system_prompt, classify_listings, generate_report
from src.metrics import build_summary, compute_listing_metrics
from src.parse_excel import parse_workbook
from src.report import assemble_report, build_demand_map_table, report_filename
from src.sb_store import (
    StorageError,
    consume_quota,
    download_xlsx,
    get_admin_client,
    get_quota,
    list_reports,
    refresh_access_token,
    save_report,
    sign_in,
    sign_out,
    sign_up,
    verify_token,
)

st.set_page_config(page_title="Avito SERP Analyzer", page_icon="📊", layout="wide")

PROMPT_PATH = config.BASE_DIR / "prompts" / "analyst.md"
MAX_UPLOAD_MB = 20

PREVIEW_COLUMNS = {
    "n": "#",
    "title": "Заголовок",
    "freshness_class": "Свежесть (косв.)",
    "freshness_basis": "Основание оценки",
    "views_today": "Просмотров сегодня (главный сигнал)",
    "views_total": "Всего просмотров (за всю жизнь)",
    "views_today_share": "Сегодня, % от общих",
    "price_raw": "Цена",
    "published_display": "Дата публ./продления",
    "age_days": "Дней с публ.",
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


@st.cache_data(show_spinner=False)
def _docx_bytes(report_md: str, title: str, niche: str, region: str) -> bytes:
    return report_to_docx(report_md, title, niche=niche, region=region)


def _render_report(result: dict):
    st.success("Отчёт готов.")
    if result.get("warning"):
        st.warning(result["warning"])
    st.markdown(result["report"])
    md_name = result["filename"]
    docx_name = md_name[:-3] + ".docx" if md_name.endswith(".md") else md_name + ".docx"
    col_md, col_docx = st.columns(2)
    with col_md:
        st.download_button(
            "Скачать отчёт (.md)",
            data=result["report"],
            file_name=md_name,
            mime="text/markdown",
            use_container_width=True,
        )
    with col_docx:
        try:
            docx = _docx_bytes(
                result["report"], "Avito SERP Analyzer",
                result.get("niche", ""), result.get("region", ""),
            )
        except DocxError as exc:
            st.error(str(exc))
        else:
            st.download_button(
                "Скачать отчёт (.docx)",
                data=docx,
                file_name=docx_name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

    if result.get("saved"):
        st.success("Сохранено в «Мои отчёты»")
    elif st.button("Сохранить в «Мои отчёты»"):
        report_id = _persist_report(
            user_id, result.get("niche", ""), result.get("region", ""),
            result["filename"], result["report"], "done",
            result.get("warning"), file_bytes,
        )
        if report_id is not None:
            result["saved"] = True
            st.rerun()


def _logout():
    session = sess.normalize_session(st.session_state.get("sb_session"))
    if session:
        sign_out(session["access_token"])
    st.session_state.pop("sb_session", None)
    st.session_state.pop("results", None)
    st.session_state.pop("stage_a_cache", None)
    st.rerun()


def _current_session() -> dict | None:
    """Проверка токена при каждом запуске скрипта; невалиден → выход.

    Перед проверкой — обновление сессии по refresh-токену, если access-token
    истекает: иначе GoTrue выкидывает из аккаунта каждый час.
    """
    session = sess.normalize_session(st.session_state.get("sb_session"))
    if session is None:
        st.session_state.pop("sb_session", None)
        return None
    if session.get("refresh_token") and sess.token_expired(session):
        try:
            refreshed = refresh_access_token(session["refresh_token"])
        except StorageError as exc:
            st.error(str(exc))
            if st.button("Выйти"):
                _logout()
            st.stop()
        if refreshed is None:
            st.session_state.pop("sb_session", None)
            return None
        session.update(refreshed)
        st.session_state["sb_session"] = session
    try:
        user = verify_token(session["access_token"])
    except StorageError as exc:
        st.error(str(exc))
        if st.button("Выйти"):
            _logout()
        st.stop()
    if user is None:
        st.session_state.pop("sb_session", None)
        return None
    session["user_id"] = getattr(user, "id", None) or session["user_id"]
    session["email"] = getattr(user, "email", None) or session["email"]
    st.session_state["sb_session"] = session
    return session


def _render_auth_gate():
    st.title("Вход / Регистрация")
    st.caption("Аккаунт хранит историю отчётов, Excel-файлы и квоту анализов.")
    tab_in, tab_up = st.tabs(["Вход", "Регистрация"])

    with tab_in:
        email = st.text_input("Email", key="auth_login_email")
        password = st.text_input("Пароль", type="password", key="auth_login_password")
        if st.button("Войти", type="primary", disabled=not (email.strip() and password)):
            try:
                sb_session, user = sign_in(email.strip(), password)
            except StorageError as exc:
                st.error(str(exc))
            else:
                st.session_state["sb_session"] = {
                    "access_token": sb_session.access_token,
                    "refresh_token": getattr(sb_session, "refresh_token", "") or "",
                    "expires_at": int(getattr(sb_session, "expires_at", 0) or 0) or None,
                    "user_id": user.id,
                    "email": user.email,
                }
                st.rerun()

    with tab_up:
        email = st.text_input("Email", key="auth_reg_email")
        password = st.text_input("Пароль (минимум 6 символов)", type="password",
                                 key="auth_reg_password")
        repeat = st.text_input("Повторите пароль", type="password", key="auth_reg_repeat")
        st.caption("Если в Supabase включено подтверждение email — активируйте аккаунт "
                   "по ссылке из письма; при авто-подтверждении сразу переходите ко входу.")
        if st.button("Зарегистрироваться", disabled=not (email.strip() and password)):
            if len(password) < 6:
                st.error("Пароль слишком короткий — минимум 6 символов.")
            elif password != repeat:
                st.error("Пароли не совпадают.")
            else:
                try:
                    result = sign_up(email.strip(), password)
                except StorageError as exc:
                    st.error(str(exc))
                else:
                    if result.get("needs_confirmation"):
                        st.success("Аккаунт создан. Подтвердите email по ссылке из письма "
                                   "и войдите на вкладке «Вход».")
                    else:
                        st.success("Аккаунт создан — можно войти на вкладке «Вход».")


def _persist_report(user_id: str, niche: str, region: str, file_name: str,
                    report_md: str, status: str, warning: str | None,
                    xlsx_bytes: bytes) -> str | None:
    """Сохранить отчёт в историю. None — не сохранён (текст в st.error)."""
    try:
        return save_report(
            user_id=user_id,
            niche=niche,
            region=region,
            model=config.get_model(),
            file_name=file_name,
            report_md=report_md,
            xlsx_bytes=xlsx_bytes,
            status=status,
            warning=warning,
        )
    except StorageError as exc:
        st.error(f"Отчёт сформирован, но не сохранён в истории: {exc}")
        return None


def _render_history(user_id: str):
    st.subheader("Мои отчёты")
    try:
        reports = list_reports(user_id)
    except StorageError as exc:
        st.error(str(exc))
        return
    if not reports:
        st.info("Отчётов пока нет — запустите анализ на вкладке «Анализ».")
        return

    st.dataframe(
        pd.DataFrame(sess.report_table_rows(reports)),
        use_container_width=True,
        hide_index=True,
    )

    labels = [sess.report_label(r) for r in reports]
    selected = st.selectbox("Отчёт", labels)
    report = reports[labels.index(selected)]

    if report.get("report_md"):
        st.markdown(report["report_md"])
    else:
        st.info("Отчёт не сформирован (LLM завершился ошибкой) — Markdown не сохранён.")

    cols = st.columns(3)
    report_md = report.get("report_md") or ""
    file_name = report.get("file_name") or ""
    if report_md:
        md_name = file_name or report_filename(report.get("niche") or "report")
        docx_name = md_name[:-3] + ".docx" if md_name.endswith(".md") else md_name + ".docx"
        with cols[0]:
            st.download_button(
                "Скачать .md", data=report_md, file_name=md_name,
                mime="text/markdown", use_container_width=True,
            )
        with cols[1]:
            try:
                docx = _docx_bytes(
                    report_md, "Avito SERP Analyzer",
                    report.get("niche") or "", report.get("region") or "",
                )
            except DocxError as exc:
                st.error(str(exc))
            else:
                st.download_button(
                    "Скачать .docx", data=docx, file_name=docx_name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )
    if report.get("file_path"):
        with cols[2]:
            try:
                xlsx = download_xlsx(user_id, report["id"])
            except StorageError as exc:
                st.error(str(exc))
            else:
                xlsx_name = (Path(file_name).stem + ".xlsx") if file_name else "upload.xlsx"
                st.download_button(
                    "Скачать Excel", data=xlsx,
                    file_name=xlsx_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )


session = _current_session()
if session is None:
    _render_auth_gate()
    st.stop()
user_id = session["user_id"]

try:
    get_admin_client()
except StorageError as exc:
    st.error(str(exc))
    st.stop()

with st.sidebar:
    st.title("📊 Avito Analyzer")
    st.caption(session.get("email") or "")
    try:
        used, quota = get_quota(user_id)
    except StorageError as exc:
        st.caption(f"Лимит: {exc}")
    else:
        st.caption(f"Отчёты: {sess.format_quota(used, quota)}")
    page = st.radio("Раздел", ["Анализ", "Мои отчёты"], horizontal=True)
    if st.button("Выйти", use_container_width=True):
        _logout()
    st.divider()

    if page == "Анализ":
        niche = st.text_input("Ниша / товар", placeholder="например: кактусы в горшках")
        region = st.text_input("Регион", placeholder="например: Москва")
        with st.expander("Дополнительно"):
            export_date = st.date_input(
                "Дата выгрузки (reference-now)",
                value=None,
                help="Если не указана — берётся текущие дата и время.",
            )
        api_key = config.get_api_key()
        st.caption(f"API key: {config.mask_key(api_key)}")
        st.caption(f"Base URL: {config.get_base_url()}")
        if not api_key:
            st.warning("Не задан ROUTERAI_API_KEY — анализ не запустится.")

if page == "Мои отчёты":
    _render_history(user_id)
    st.stop()

st.title("Анализ поисковой выдачи Avito")
st.caption(
    "Excel-выгрузка → метрики и бакеты свежести (код) → кластеризация спроса (LLM) "
    "→ Markdown-отчёт с секциями 1–9."
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
st.caption(
    f"⏱ Время парсинга: {parsed['parsed_at'].strftime('%d.%m.%Y %H:%M')} — "
    "«Просмотров сегодня» привязано к этому моменту."
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

api_key = config.get_api_key()
force_run = bool(st.session_state.pop("force_run", False)) and bool(
    niche.strip() and region.strip() and api_key
)
run = st.button("Анализ", type="primary",
                disabled=not (niche.strip() and region.strip() and api_key)) or force_run
if not (niche.strip() and region.strip()):
    st.caption("Укажите нишу и регион в боковой панели.")
elif not api_key:
    st.caption("Задайте ROUTERAI_API_KEY для запуска анализа.")

resolved_model = config.get_model()
result_key = "|".join([
    file_hash, niche.strip(), region.strip(), resolved_model,
    str(export_date or "now"),
])
results: dict = st.session_state.setdefault("results", {})
stage_a_cache: dict = st.session_state.setdefault("stage_a_cache", {})

if run or (result_key in results):
    if result_key in results:
        _render_report(results[result_key])
    else:
        try:
            quota_ok = consume_quota(user_id)
        except StorageError as exc:
            st.error(str(exc))
            st.stop()
        if not quota_ok:
            st.error(sess.quota_exceeded_message())
            st.stop()

        reference_now = _reference_now(export_date)
        progress = st.progress(0.0, text="Парсинг и метрики…")
        status = st.empty()
        stage_key = None
        file_name = report_filename(niche.strip())

        try:
            listings = [compute_listing_metrics(r, reference_now) for r in records]
            summary = build_summary(listings)
            progress.progress(0.15, text="Метрики посчитаны")
            status.caption(
                "Свежесть (косвенно): "
                + ", ".join(f"{k}: {v}" for k, v in summary["class_counts"].items())
            )

            status.info("Стадия A — кластеризация объявлений (LLM)…")
            stage_key = f"{file_hash}|{resolved_model}"
            if stage_key in stage_a_cache:
                classifications = stage_a_cache[stage_key]
                progress.progress(0.55, text="Стадия A — из кэша")
            else:
                def stage_a_progress(done: int, total: int):
                    progress.progress(0.15 + 0.4 * done / max(total, 1),
                                      text=f"Стадия A: чанк {done}/{total}")

                classifications, stage_a_error = classify_listings(
                    listings, progress=stage_a_progress
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
                                       niche.strip(), region.strip(), reference_now,
                                       parsed_at=parsed["parsed_at"],
                                       classifications=classifications)

            status.info("Стадия B — генерация отчёта (LLM)…")
            system_prompt = build_system_prompt(
                niche.strip(), region.strip(), _load_prompt()
            )
            stage_b_md, grounding_warning = generate_report(system_prompt, digest_text)
            progress.progress(0.9, text="Сборка отчёта…")

            report_md = assemble_report(
                stage_b_md, demand_map_md,
                niche=niche.strip(), region=region.strip(),
                total=len(listings), model=resolved_model,
                parsed_at=parsed["parsed_at"],
            )
            results[result_key] = {
                "report": report_md,
                "filename": file_name,
                "warning": grounding_warning,
                "niche": niche.strip(),
                "region": region.strip(),
                "saved": False,
            }
            progress.progress(1.0, text="Готово")
            status.empty()
            if _persist_report(
                user_id, niche.strip(), region.strip(), file_name,
                report_md, "done", grounding_warning, file_bytes,
            ) is not None:
                results[result_key]["saved"] = True
            _render_report(results[result_key])

        except LLMError as exc:
            progress.empty()
            status.empty()
            _persist_report(
                user_id, niche.strip(), region.strip(), file_name,
                "", "failed", None, file_bytes,
            )
            st.error(str(exc))
            if st.button("Повторить"):
                if stage_key:
                    stage_a_cache.pop(stage_key, None)
                st.session_state["force_run"] = True
                st.rerun()
        except FileNotFoundError as exc:
            progress.empty()
            status.empty()
            _persist_report(
                user_id, niche.strip(), region.strip(), file_name,
                "", "failed", None, file_bytes,
            )
            st.error(str(exc))
        except Exception as exc:
            progress.empty()
            status.empty()
            _persist_report(
                user_id, niche.strip(), region.strip(), file_name,
                "", "failed", None, file_bytes,
            )
            st.error(f"Ошибка анализа: {exc}")
