"""Клиент OpenAI-совместимого API (routerai.ru): стадия A (JSON-кластеризация) и стадия B (отчёт)."""

from __future__ import annotations

import json
import re
from typing import Callable

from . import config

ALLOWED_KINDS = {"товарный", "характеристический", "сценарный", "проблемный", "коммерческий"}
CHUNK_SIZE = 8
DESCRIPTION_LIMIT = 1500

STAGE_A_SYSTEM = """Ты — аналитик поисковой выдачи Avito. Твоя задача — классифицировать объявления.

Для каждого объявления верни JSON-объект с полями:
- "n": номер объявления (как во входных данных);
- "b2b": один из "B2C", "B2B", "B2C+B2B";
- "clusters": список объектов {"kind": ..., "tag": ...}, где kind — ровно один из
  "товарный", "характеристический", "сценарный", "проблемный", "коммерческий",
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


def generate_report(system_prompt: str, digest_text: str, model: str | None = None) -> str:
    """Стадия B: финальный Markdown-отчёт (секции 1–10)."""
    resolved_model = config.get_model(model)
    client = _client()
    text = _chat(client, resolved_model, system_prompt, digest_text, temperature=0.3)
    if not re.search(r"^#+\s*1[\.\s]", text, flags=re.MULTILINE):
        raise LLMError("LLM не вернул структурированный отчёт (нет секции 1) — повторите попытку.")
    return text
