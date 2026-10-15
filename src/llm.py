"""Клиент OpenAI-совместимого API (routerai.ru): стадия A (JSON-кластеризация) и стадия B (отчёт)."""

from __future__ import annotations

import json
import re
from typing import Callable

from . import config
from .dates import NO_FRESHNESS

ALLOWED_KINDS = {"товарный", "характеристический", "сценарный", "проблемный", "коммерческий"}
CHUNK_SIZE = 8
DESCRIPTION_LIMIT = 1500

NO_DATA_MARKER = "[НЕТ ДАННЫХ]"
HYPOTHESIS_MARKER = "[НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ГИПОТЕЗ]"
DECISION_MARKER = "[НЕДОСТАТОЧНО ДАННЫХ ДЛЯ РЕШЕНИЯ]"
GROUNDING_MARKERS = (NO_DATA_MARKER, HYPOTHESIS_MARKER, NO_FRESHNESS, DECISION_MARKER)
MIN_HYPOTHESES = 5
MAX_HYPOTHESES = 10
CONCLUSION_POINTS = 7
GROUNDING_WARNING_PREFIX = "Часть гипотез/выводов не прошла проверку на привязку к данным: "

STAGE_A_SYSTEM = """Ты — аналитик поисковой выдачи Avito. Твоя задача — классифицировать объявления.

Для каждого объявления верни JSON-объект с полями:
- "n": номер объявления (как во входных данных);
- "b2b": один из "B2C", "B2B", "B2C+B2B";
- "clusters": список объектов {"kind": ..., "tag": ...}, где kind — ровно один из
  "товарный" (конкретный товар, напр. «мох в коробках»), "характеристический"
  (характеристики: размер, материал), "сценарный" (сценарий: подарок, озеленение),
  "проблемный" (проблема: «не приживается»), "коммерческий" (условия: опт, доставка),
  а tag — короткий тег спроса (2–4 слова, по-русски, отражает запрос покупателя);
- "queries": список 1–5 поисковых запросов покупателя, которые можно извлечь из текста;
- "usp": главное УТП объявления (строка или null);
- "scenario": сценарий покупки в 1 предложение (строка или null).

Ответ — только валидный JSON-массив объектов для ВСЕХ переданных объявлений, без markdown и пояснений."""


class LLMError(Exception):
    """Понятная пользователю ошибка LLM-стадии."""


def _client():
    from openai import OpenAI

    key = config.get_api_key()
    if not key:
        raise LLMError("Не задан ROUTERAI_API_KEY — добавьте ключ в настройки (env Coolify или .env).")
    try:
        return OpenAI(api_key=key, base_url=config.get_base_url(),
                      timeout=config.LLM_TIMEOUT, max_retries=0)
    except Exception as exc:  # pragma: no cover
        raise LLMError(f"Не удалось создать клиент LLM: {exc}") from exc


def _friendly_error(exc: Exception) -> LLMError:
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return LLMError("Ошибка авторизации API — проверьте ROUTERAI_API_KEY.")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("Превышен лимит запросов API — подождите и повторите.")
    if isinstance(exc, openai.APITimeoutError):
        return LLMError("Таймаут ответа LLM — повторите попытку.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("Не удалось подключиться к API — проверьте ROUTERAI_BASE_URL.")
    if isinstance(exc, openai.BadRequestError):
        return LLMError(f"Некорректный запрос к LLM: {exc}")
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"API вернул ошибку {exc.status_code}: {exc.message}")
    if isinstance(exc, openai.OpenAIError):
        return LLMError(f"Ошибка LLM: {exc}")
    return LLMError(f"Неожиданная ошибка LLM: {exc}")


def _chat(client, model: str, system: str, user: str, temperature: float = 0.0) -> str:
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        raise _friendly_error(exc) from exc
    content = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not content:
        raise LLMError("LLM вернул пустой ответ — повторите попытку.")
    return content


def _extract_json(text: str):
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("Невалидный JSON в ответе стадии A.")


def validate_stage_a(parsed, expected_ns: set[int]) -> dict[int, dict]:
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list):
        raise LLMError("Стадия A: ответ не массив.")
    result: dict[int, dict] = {}
    for item in parsed:
        if not isinstance(item, dict):
            raise LLMError("Стадия A: элемент не объект.")
        n = item.get("n")
        if isinstance(n, str) and n.strip().isdigit():
            n = int(n.strip())
        if not isinstance(n, int) or n not in expected_ns:
            raise LLMError("Стадия A: некорректный номер объявления.")
        clusters = item.get("clusters")
        if not isinstance(clusters, list):
            raise LLMError("Стадия A: нет clusters.")
        normalized = []
        for cluster in clusters:
            if not isinstance(cluster, dict):
                raise LLMError("Стадия A: некорректный кластер.")
            kind = str(cluster.get("kind") or "").strip().lower().rstrip("*")
            tag = str(cluster.get("tag") or "").strip()
            if kind not in ALLOWED_KINDS or not tag:
                raise LLMError("Стадия A: некорректный кластер.")
            normalized.append({"kind": kind, "tag": tag})
        result[n] = {
            "b2b": item.get("b2b") if item.get("b2b") in ("B2C", "B2B", "B2C+B2B") else "B2C",
            "clusters": normalized,
            "queries": [str(q) for q in item.get("queries") or []][:5],
            "usp": item.get("usp"),
            "scenario": item.get("scenario"),
        }
    missing = expected_ns - set(result)
    if missing:
        raise LLMError(f"Стадия A: не вернули объявления {sorted(missing)}.")
    return result


def _stage_a_chunk_payload(chunk: list[dict]) -> str:
    lines = []
    for record in chunk:
        description = (record.get("description") or "")[:DESCRIPTION_LIMIT]
        lines.append(
            f"#{record['n']} | {record.get('title') or '[без заголовка]'} | "
            f"цена: {record.get('price_raw') or '[НЕТ ДАННЫХ]'} | "
            f"категории: {' / '.join(record.get('categories') or []) or '[НЕТ ДАННЫХ]'} | "
            f"описание: {description or '[НЕТ ДАННЫХ]'}"
        )
    return "Классифицируй объявления:\n" + "\n".join(lines)


def classify_listings(listings: list[dict], model: str | None = None,
                      progress: Callable[[int, int], None] | None = None
                      ) -> tuple[dict[int, dict] | None, str | None]:
    """Стадия A: классификация каждого объявления.

    Возвращает (результат, None) или (None, причина) — фолбэк для карты спроса.
    """
    if not listings:
        return {}, None
    resolved_model = config.get_model(model)
    client = _client()
    expected_ns = {r["n"] for r in listings}
    results: dict[int, dict] = {}
    first_error: str | None = None

    chunks = [listings[i:i + CHUNK_SIZE] for i in range(0, len(listings), CHUNK_SIZE)]
    for ci, chunk in enumerate(chunks):
        chunk_ns = {r["n"] for r in chunk}
        payload = _stage_a_chunk_payload(chunk)
        last_error: LLMError | None = None
        for attempt in (1, 2):
            try:
                raw = _chat(client, resolved_model, STAGE_A_SYSTEM, payload, temperature=0.0)
                parsed = _extract_json(raw)
                results.update(validate_stage_a(parsed, chunk_ns))
                last_error = None
                break
            except LLMError as exc:
                last_error = exc
                if first_error is None:
                    first_error = str(exc)
                payload = payload + (
                    "\n\nПРЕДЫДУЩИЙ ОТВЕТ БЫЛ НЕВАЛИДЕН ("
                    f"{exc}). Верни строго валидный JSON-массив для всех объявлений."
                )
        if last_error is not None:
            if progress:
                progress(ci + 1, len(chunks))
            return None, first_error
        if progress:
            progress(ci + 1, len(chunks))

    if set(results) != expected_ns:
        return None, first_error or "Стадия A: неполный результат."
    return results, None


def build_system_prompt(niche: str, region: str, prompt_template: str) -> str:
    return (
        prompt_template
        .replace("[НИША / ТОВАР]", niche)
        .replace("[РЕГИОН]", region)
    )


# --- Валидация привязки секций 8–9 к данным (Grounding) -------------------

# Разряды через пробел/nbsp: «1 200» → «1200», «12 345 678» → «12345678».
# Группа справа ровно 3 цифры, иначе это два отдельных числа («10 15»).
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d)[\s  ](?=\d{3}(?!\d))")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

_RE_SECTION_1 = re.compile(r"^#+\s*1[\.\s]", flags=re.MULTILINE)
_RE_SECTION_8 = re.compile(r"^##\s*8(?:[.:]|\s)", flags=re.MULTILINE)
_RE_SECTION_9 = re.compile(r"^##\s*9[.:]", flags=re.MULTILINE)
_RE_HYP_HEADER = re.compile(r"^\*\*Гипотеза\s+(\d+):", flags=re.MULTILINE)
_RE_HYP_ANY = re.compile(r"Гипотеза\s*\d+")
_RE_HYP_LINE = re.compile(r"^\*\*Гипотеза\s+\d+:")
_RE_SINGLETON_BASIS = re.compile(
    r"(?<![\d.,])1\s+(?:[^\s;.,]+\s+){0,3}объявлени", flags=re.IGNORECASE
)
# Темы «дефекты/б/у» в заголовке гипотезы: пометки состояния от продавцов б/у — не спрос.
_RE_FORBIDDEN_TOPIC = re.compile(
    r"дефект|биты|битой|побит|б/у|б[- ]?у\b|бывш|уцен|царапин|потёрт|"
    r"следы эксплуатации|не новый",
    flags=re.IGNORECASE,
)
_RE_POINT = re.compile(r"^\s*(\d+)\)", flags=re.MULTILINE)
_RE_POINT_BAD = re.compile(r"^\s*(\d+)\.", flags=re.MULTILINE)


def normalize_numbers(text: str) -> set[float]:
    r"""Нормализованные числовые токены текста.

    1) убираются разделители разрядов («1 200» → 1200); 2) токен `\d+([.,]\d+)?`
    → float (запятая → точка); 3) `%`/`≈`/`~` на токен не влияют;
    4) диапазон «10–15» даёт оба токена; 5) «тыс.»/«млн» не раскрываются,
    дроби «2/3» не склеиваются — задокументированное ограничение.
    """
    cleaned = _THOUSANDS_SEP_RE.sub("", text)
    return {float(m.group(0).replace(",", ".")) for m in _NUMBER_RE.finditer(cleaned)}


def _has_marker(text: str) -> bool:
    return any(marker in text for marker in GROUNDING_MARKERS)


def _slice_blocks(body: str, headers: list) -> list[str]:
    blocks = []
    for i, header in enumerate(headers):
        start = header.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        blocks.append(body[start:end])
    return blocks


def _body_after_header(text: str, header) -> str:
    """Текст секции после заголовка, без остатка строки заголовка («## 9.» + название)."""
    rest = text[header.end():]
    newline = rest.find("\n")
    return rest[newline:] if newline != -1 else ""


def validate_grounding(text: str, digest_text: str) -> list[str]:
    """Проверка секций 8 и 9 по политике Grounding. Пустой список = OK."""
    violations: list[str] = []
    digest_tokens = normalize_numbers(digest_text)

    section_8 = _RE_SECTION_8.search(text)
    section_9 = _RE_SECTION_9.search(text)

    if section_8 is None:
        violations.append("нет секции 8 «Гипотезы» (заголовок вида «## 8. Гипотезы»)")
    elif section_9 is not None and section_8.start() > section_9.start():
        violations.append("секция 8 идёт после секции 9 — нарушена структура отчёта")
        section_8 = None

    if section_9 is None:
        violations.append(
            "нет секции 9 «Решение о входе» (заголовок вида «## 9. Решение о входе»)"
        )
        return violations

    if section_8 is not None:
        body_8 = _body_after_header(text[:section_9.start()], section_8)
        headers = list(_RE_HYP_HEADER.finditer(body_8))
        for line in body_8.splitlines():
            if _RE_HYP_ANY.search(line) and not _RE_HYP_LINE.match(line):
                violations.append(
                    "гипотеза оформлена не по шаблону (нужен заголовок вида «**Гипотеза N: …**»): "
                    + line.strip()[:80]
                )
        count = len(headers)
        if count < MIN_HYPOTHESES or count > MAX_HYPOTHESES:
            violations.append(f"гипотез: {count} — требуется от {MIN_HYPOTHESES} до {MAX_HYPOTHESES}")
        for header, block in zip(headers, _slice_blocks(body_8, headers)):
            label = f"гипотеза {header.group(1)}"
            title = block.split("**", 1)[0]
            if _RE_FORBIDDEN_TOPIC.search(title):
                violations.append(
                    f"{label}: тема дефектов/б/у в заголовке запрещена — пометки "
                    "состояния ставят продавцы б/у, это не спрос; переформулируй "
                    "про спрос, цену, кластеры, сценарии или конкуренцию"
                )
            if "Основание" not in block:
                violations.append(f"{label}: нет подстроки «Основание»")
            if "Проверка" not in block:
                violations.append(f"{label}: нет подстроки «Проверка»")
            crit_at = block.find("Критерий успеха")
            if crit_at == -1:
                violations.append(f"{label}: нет подстроки «Критерий успеха»")
            else:
                # Критерий — цель теста: обязан содержать число-порог; маркер не освобождает.
                criterion = block[crit_at:]
                newline = criterion.find("\n")
                if newline != -1:
                    criterion = criterion[:newline]
                if not normalize_numbers(criterion):
                    violations.append(
                        f"{label}: в «Критерии успеха» нет числового порога"
                    )
            basis_at = block.find("Основание")
            if basis_at != -1:
                check_at = block.find("Проверка", basis_at)
                basis = block[basis_at:check_at] if check_at != -1 else block[basis_at:]
                # Основание из единичного объявления («1 старое объявление…») —
                # признак надуманной гипотезы: нужен агрегат или маркер.
                if _RE_SINGLETON_BASIS.search(basis) and normalize_numbers(basis) <= {1.0}:
                    violations.append(
                        f"{label}: «Основание» опирается на единичное объявление — "
                        f"нужен агрегат из дайджеста или маркер {HYPOTHESIS_MARKER}"
                    )
            if _has_marker(block):
                continue  # маркер данных освобождает от цитаты дайджеста
            tokens = normalize_numbers(block)
            if not tokens:
                violations.append(f"{label}: после заголовка нет ни одной цифры и нет маркера данных")
            elif not (tokens & digest_tokens):
                violations.append(f"{label}: цифры не встречаются в дайджесте — нет цитаты из данных")

    body_9 = _body_after_header(text, section_9)
    points = list(_RE_POINT.finditer(body_9))

    # Блок вердикта — текст до первого пункта «N)».
    verdict_block = body_9[:points[0].start()] if points else body_9
    if not verdict_block.strip().startswith("**Вердикт:"):
        violations.append(
            "§9: нет блока вердикта — первая строка секции должна начинаться с «**Вердикт:** …»"
        )
    elif DECISION_MARKER not in verdict_block:
        verdict_tokens = normalize_numbers(verdict_block)
        if not (verdict_tokens & digest_tokens):
            violations.append(
                "п.9: вердикт не подкреплён цифрой из дайджеста и нет маркера "
                f"«{DECISION_MARKER}»"
            )

    bad_points = _RE_POINT_BAD.findall(body_9)
    if bad_points:
        violations.append(
            "пункты §9 оформлены точкой вместо скобки ("
            + ", ".join(f"«{p}.»" for p in bad_points)
            + ") — нужна нумерация 1)…7)"
        )
    numbers = [int(p.group(1)) for p in points]
    if numbers != list(range(1, CONCLUSION_POINTS + 1)):
        violations.append(
            f"§9: найдены пункты {numbers or '—'} — требуется ровно {CONCLUSION_POINTS} "
            f"пунктов нумерацией 1)–{CONCLUSION_POINTS})"
        )
    for point, body in zip(points, _slice_blocks(body_9, points)):
        number = int(point.group(1))
        if not 1 <= number <= CONCLUSION_POINTS:
            continue
        if _has_marker(body):
            continue
        tokens = normalize_numbers(body)
        if number < CONCLUSION_POINTS:
            if not tokens:
                violations.append(f"п.9.{number}: нет цифры и нет маркера данных")
        elif not (tokens & digest_tokens):
            violations.append(
                "п.9.7: действие не подкреплено цифрой из дайджеста и нет маркера данных"
            )

    return violations


def _grounding_feedback(violations: list[str]) -> str:
    return (
        "\n\nПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРОШЁЛ ПРОВЕРКУ НА ПРИВЯЗКУ К ДАННЫМ:\n"
        + "\n".join(f"- {v}" for v in violations)
        + "\nИсправь отчёт: соблюдай строгий формат секций 8 и 9 и цитируй цифры из дайджеста."
    )


def _has_section_one(text: str) -> bool:
    return bool(_RE_SECTION_1.search(text))


def generate_report(system_prompt: str, digest_text: str,
                    model: str | None = None) -> tuple[str, str | None]:
    """Стадия B: финальный Markdown-отчёт (секции 1–9) + предупреждение Grounding.

    Один повторный вызов с feedback при нарушениях секций 8–9; после повторного
    провала текст принимается, warning сообщает о непройденной проверке.
    """
    resolved_model = config.get_model(model)
    client = _client()
    text = _chat(client, resolved_model, system_prompt, digest_text, temperature=0.3)
    if not _has_section_one(text):
        raise LLMError("LLM не вернул структурированный отчёт (нет секции 1) — повторите попытку.")

    violations = validate_grounding(text, digest_text)
    if not violations:
        return text, None

    retry = _chat(client, resolved_model, system_prompt,
                  digest_text + _grounding_feedback(violations), temperature=0.3)
    if _has_section_one(retry):
        retry_violations = validate_grounding(retry, digest_text)
        if not retry_violations:
            return retry, None
        text, violations = retry, retry_violations

    return text, GROUNDING_WARNING_PREFIX + "; ".join(violations)
