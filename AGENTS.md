# AGENTS.md — Avito SERP Analyzer

Проект: исследование ниши Avito для решения о входе. Excel-выгрузка парсера + ниша/регион →
Markdown-отчёт секций 1–9 с вердиктом. Метрики считает код, аналитику пишет LLM
(OpenAI-совместимый RouterAI API). SaaS: Supabase-аккаунты, квоты, история, DOCX-экспорт.

## Команды

- Тесты: `python -m pytest tests/` (212 проходят) — в `.venv` (`source .venv/bin/activate`)
- Локально: `streamlit run app.py` → http://localhost:8501
- Линтера нет; стиль — PEP8, комментарии по-русски (не добавлять, если не просят)
- Docker: `docker build -t avito-analyzer .` (проверено, контейнер healthy)
- Деплой: Coolify из GitHub (origin `andkuv001-ui/avito-analyzer`, branch `main`);
  env в Coolify не менялись — **ручной ops-шаг pending**: заменить
  `SUPABASE_SERVICE_ROLE_KEY` на service_role/`sb_secret_` ключ и redeploy

## Архитектура (ключевые файлы)

- `app.py` — Streamlit UI; промпт читает из `prompts/analyst.md` (`PROMPT_PATH`), плейсхолдеры
  `[НИША / ТОВАР]`, `[РЕГИОН]` подставляет `build_system_prompt`
- `prompts/analyst.md` — системный промпт Стадии B (структура §1–9 + ЖЁСТКИЕ ПРАВИЛА)
- `src/llm.py` — `STAGE_A_SYSTEM` (классификация, JSON), `generate_report` (Стадия B),
  **`validate_grounding`** (валидация секций 8–9, 1 retry с feedback, иначе warning в UI),
  `build_system_prompt`, `validate_stage_a`
- `src/metrics.py` — свежесть, сегменты (тертили), `build_fact_pack` (FACT_NAMES),
  `normalize_cluster_tag`/`tag_key` (очистка тегов), `kpi_caps` (реалистичные пороги)
- `src/digest.py` — `build_digest` + `rules(parsed_at)` (правила интерпретации с месяцем парсинга)
- `src/report.py` — `assemble_report`, `build_demand_map_table` (карта спроса из кода)
- `src/parse_excel.py`, `src/dates.py`, `src/session.py`, `src/sb_store.py`, `src/docx_export.py`, `src/config.py`

## Состояние: усилено сохранение отчётов (403 RLS и потеря отчётов)

План: `.kilo/plans/1792132606672-report-save-hardening.md`.

1. **Preflight ключа**: `service_key_role`/`assert_service_key` (`src/sb_store.py`)
   вызываются в `get_admin_client()`; anon/publishable в `SUPABASE_SERVICE_ROLE_KEY`
   → `SupabaseMisconfiguredKey` с текстом «Замените на service_role / sb_secret_ ключ».
   Неизвестный формат ключа не блокируется (role `None`). В `app.py` preflight сразу
   после логина: `st.error(str(exc))` + `st.stop()` — блок до анализа вместо 403 RLS
   в момент сохранения.
2. **`save_report` не теряет отчёт**: upload Excel нефатален — при падении строка
   всё равно вставляется с `file_path=null` и warning «Excel-файл не сохранён в
   истории: …; <исходный warning>»; падение insert по-прежнему `StorageError`
   («Не удалось сохранить отчёт…»). Путь Storage — `{user_id}/{report_id}.xlsx`
   в bucket `reports` (без двойного префикса `reports/`, раньше ломал RLS «own files read»).
   Старые строки со старым `file_path` качаются как прежде (путь в строке).
3. **Повтор сохранения**: в кэше `results[result_key]` поле `saved`; при
   `saved=False` под отчётом кнопка «Сохранить в «Мои отчёты»» (повторный
   `_persist_report` → `saved=True` → rerun → `st.success`).
4. **Упавшие прогоны**: ветки `except FileNotFoundError` и `except Exception`
   тоже вызывают `_persist_report(..., "", "failed", None, file_bytes)` —
   прогоны попадают в историю с пустым MD.
5. **Excel в истории**: кнопка «Скачать Excel» отдаёт `stem(file_name) + ".xlsx"`,
   а не имя `.md`.
6. `_persist_report` возвращает `report_id | None` (None → текст в `st.error`).

Вне объёма (как в плане): refresh-токены/сессии >1 ч, INSERT-политики RLS,
миграции схемы, платёжный шлюз, ретеншн.

## Состояние: закрыты 10 критических замечаний по качеству анализа (коммит `f1eac2c`, запушен)

Принцип: правило пишется ТРИжды — промпт (`analyst.md`) + дайджест (`digest.py::rules`) +
где возможно код-валидация в `llm.py::validate_grounding` (ловит даже при плохой дисциплине LLM).

1. **Сезонность**: правило «СЕЗОННОСТЬ ОБЯЗАТЕЛЬНА» (месяц парсинга в дайджесте, русские месяцы
   в `digest.py`); violation: основание «0 свежих/нет свежих/без свежих» без слова «сезон» в блоке гипотезы.
2. **Товар клиента**: правило «ТОВАР КЛИЕНТА» — гипотезы только под позиционирование
   `[НИША / ТОВАР]`, чужой товарный класс (пример: ортопедические школьные при городском) → нецелевой трафик.
   Хардкода конкретных брендов НЕТ (проверено grep: Xiaomi и т.п. в промте отсутствует).
3. **Малая база**: singleton-правило расширено на 1–2 объявления (`_SINGLETON_BASIS`, `_SINGLETON_MAX = {1.0, 2.0}`).
4. **Сервис ≠ гипотеза**: `_RE_SERVICE_TOPIC` (доставк/оплат/самовывоз/пункт выдачи) в заголовке
   гипотезы → violation; исключение — ниша-услуга (`_RE_DELIVERY_NICHE`, ниша парсится из дайджеста
   `_RE_DIGEST_NICHE` по строке `Ниша/товар:`).
5. **KPI-реализм**: факт `[реалистичные пороги]` в `FACT_NAMES` = 2× медианы «Просмотров сегодня»
   сегмента (`kpi_caps`, `KPI_CAP_FACTOR=2`); violation: порог «просмотров/день» в «Критерии успеха»
   > потолка (`_RE_KPI_CAPS`, `_RE_VIEWS_PER_DAY`); обход — бюджет на платные услуги в «Проверке»
   (`_RE_PAID_BUDGET`).
6. **Сегменты без дырок**: правило «ТЕРТИЛИ БЕЗ ПРОБЕЛОВ» — границы только из таблицы дайджеста,
   min–max ≠ границы (в коде сегменты и так сплошные).
7. **Старые vs свежие**: правило «ВЫВОД «СТАРЫЕ ЭФФЕКТИВНЕЕ» — С ОГОВОРКОЙ» (накопленный вес,
   история продвижения) — уровень промпта/дайджеста, без код-проверки.
8. **Юнит-экономика**: обязательная оговорка в §9 (regex `юнит-эконом|себестоимост|марж\w*`)
   + **безусловный «ВХОДИТЬ» запрещён** → только «ВХОДИТЬ ПРИ УСЛОВИИ …» или «НЕ ВХОДИТЬ».
9. **Очистка карты спроса**: `normalize_cluster_tag` в `metrics.py` — мусор
   (бесплатно/дефекты/следы эксплуатации/уценка/б/у) → None, размеры → тег «размеры»;
   дубли регистра склеиваются по casefold-ключу. Применяется в 3 местах: `validate_stage_a`
   (Стадия A), `build_demand_map_table` (report.py), `cluster_price_stats` + факты (metrics.py).
   Очистка также продублирована в промпте Стадии A (запрос покупателя ≠ пометка продавца).
10. **Вердикт по товару/сегменту**: вердикт обязан называть целевой ценовой сегмент
    (regex `сегмент|дешёв|средн|дорог` в блоке вердикта); без данных → маркер
    `[НЕДОСТАТОЧНО ДАННЫХ ДЛЯ РЕШЕНИЯ]`.

Обязательные фразы в §9 (проверяются валидацией): «второй этап воронки» + юнит-экономика.

## Тесты (добавлены в рамках `f1eac2c`)

- `tests/test_prompt_template.py` — гард: ключевые правила не вычищены из `prompts/analyst.md`
- `tests/test_llm_stage_a.py` — очистка тегов на Стадии A
- `tests/test_llm_grounding.py` — все новые violation-проверки (сезон, сервис, KPI-cap,
  юнит-экономика, сегмент, безусловный ВХОДИТЬ); фикстуры `_verdict`/`_report` обновлены
  (по умолчанию «ВХОДИТЬ ПРИ УСЛОВИИ … сегмент: дешёвые», `econ_caveat=True`)
- `tests/test_metrics.py`, `tests/test_report.py`, `tests/test_digest.py` — очистка карты,
  `[реалистичные пороги]`, правила `rules()`

## Важные нюансы

- Сегменты: тертили (`PRICE_SEGMENT_MIN_BASE=9`), малая база → `[НЕТ ДАННЫХ]`.
- `FACT_NAMES` упорядочен — тесты индексируют факты по позиции (`facts[8]` = «кластеры без свежих»);
  новые факты добавлять ТОЛЬКО в конец.
- Валидация Grounding: 1 retry с feedback → при провале отчёт принимается, `st.warning`.
- `python -c "import app"` падает (`st.stop()` вне рантайма) — это норма, не баг; проверять через
  `streamlit run` + `/_stcore/health`.
- Незакоммичено: только untracked `.kilo/` (локальные планы, в git исторически не входит).
- Деплой не выполнен (проверена только сборка/запуск локально); план — Coolify.
