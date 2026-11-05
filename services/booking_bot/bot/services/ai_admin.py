"""AI Admin Service — история диалога + вызов OpenRouter API."""
import asyncio
import json
import logging
from typing import Optional

import aiohttp

from bot.config import OPENROUTER_AI_ADMIN_KEY

logger = logging.getLogger(__name__)

MODELS = [
    "qwen/qwen3-235b-a22b-2507",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-120b",
]
OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
MAX_OWNER_MSGS  = 15   # компакция после этого числа сообщений от владельца

_histories:       dict[int, list[dict]] = {}
_lock             = asyncio.Lock()
_pending_actions: dict[str, dict] = {}   # action_id -> {name, args, business_id, file_id}


# ─── ИСТОРИЯ ──────────────────────────────────────────────────────────────────

async def get_history(user_id: int) -> list[dict]:
    async with _lock:
        return list(_histories.get(user_id, []))


async def add_to_history(user_id: int, role: str, content) -> None:
    async with _lock:
        _histories.setdefault(user_id, []).append({"role": role, "content": content})


async def clear_history(user_id: int) -> None:
    async with _lock:
        _histories.pop(user_id, None)


def _count_user_msgs(history: list[dict]) -> int:
    return sum(1 for m in history if m["role"] == "user")


async def _compact(user_id: int, system_prompt: str) -> None:
    """Сжать историю в 3-5 предложений."""
    async with _lock:
        history = list(_histories.get(user_id, []))
    if not history:
        return
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": (
            "Сожми эту историю диалога в 3-5 предложений на русском. "
            "Сохрани ключевые факты: что менялось, что решили. "
            "Только сжатый текст, без предисловий."
        )},
    ]
    try:
        summary = await _call_api(messages, tools=None)
        if isinstance(summary, str):
            async with _lock:
                _histories[user_id] = [
                    {"role": "assistant",
                     "content": f"[Сводка предыдущего разговора]\n{summary}"}
                ]
            logger.info("AI admin history compacted for user %s", user_id)
    except Exception as exc:
        logger.error("Compaction failed for user %s: %s", user_id, exc)
        async with _lock:
            _histories.pop(user_id, None)


# ─── OPENROUTER ───────────────────────────────────────────────────────────────

async def _call_api(messages: list[dict], tools: Optional[list[dict]]) -> "str | dict":
    """
    Вызов OpenRouter с fallback по моделям при 429/404.
    Возвращает str (текст) или dict (tool call).
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_AI_ADMIN_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://booking-bot.local",
    }

    last_error = None
    for model in MODELS:
        payload: dict = {"model": model, "messages": messages, "temperature": 0.3, "provider": {"ignore": ["Google"]}}
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_URL, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as resp:
                    if resp.status in (429, 403, 404, 503):
                        text = await resp.text()
                        logger.warning("Model %s returned %s, trying next: %s", model, resp.status, text[:100])
                        last_error = RuntimeError(f"OpenRouter {resp.status} ({model}): {text[:200]}")
                        await asyncio.sleep(2)
                        continue
                    if resp.status != 200:
                        raise RuntimeError(f"OpenRouter {resp.status}: {(await resp.text())[:300]}")
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Model %s connection error: %s, trying next", model, e)
            last_error = e
            continue

        logger.info("AI response from model: %s", model)
        msg = data["choices"][0]["message"]
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            return {
                "name":        tc["function"]["name"],
                "arguments":   json.loads(tc["function"]["arguments"]),
                "tool_call_id": tc["id"],
                "raw_message": msg,
            }
        return msg.get("content") or ""

    raise last_error or RuntimeError("All models failed")


def _append_assistant(user_id: int, result: "str | dict") -> None:
    """Добавить ответ ассистента в историю (с сохранением tool_calls)."""
    if isinstance(result, str):
        _histories.setdefault(user_id, []).append({"role": "assistant", "content": result})
    else:
        raw_msg = result["raw_message"]
        entry = {"role": "assistant", "content": raw_msg.get("content")}
        if raw_msg.get("tool_calls"):
            entry["tool_calls"] = raw_msg["tool_calls"]
        _histories.setdefault(user_id, []).append(entry)


# ─── ОСНОВНАЯ ТОЧКА ВХОДА ─────────────────────────────────────────────────────

async def process_message(
    user_id:       int,
    system_prompt: str,
    user_content,           # str или list (multimodal)
    tools:         list[dict],
) -> "str | dict":
    await add_to_history(user_id, "user", user_content)
    history  = await get_history(user_id)
    messages = [{"role": "system", "content": system_prompt}] + history

    result = await _call_api(messages, tools)

    async with _lock:
        _append_assistant(user_id, result)

    updated = await get_history(user_id)
    if _count_user_msgs(updated) >= MAX_OWNER_MSGS:
        asyncio.create_task(_compact(user_id, system_prompt))

    return result


async def process_tool_result(
    user_id:       int,
    system_prompt: str,
    tool_call_id:  str,
    tool_name:     str,
    tool_output:   str,
    tools:         list[dict],
) -> "str | dict":
    """Продолжить диалог после выполнения tool — добавить результат и снова вызвать AI."""
    async with _lock:
        _histories.setdefault(user_id, []).append({
            "role":         "tool",
            "tool_call_id": tool_call_id,
            "name":         tool_name,
            "content":      tool_output,
        })
        history = list(_histories[user_id])

    messages = [{"role": "system", "content": system_prompt}] + history
    result = await _call_api(messages, tools)

    async with _lock:
        _append_assistant(user_id, result)

    return result


# ─── ОЖИДАЮЩИЕ ДЕЙСТВИЯ ───────────────────────────────────────────────────────

def store_pending_action(
    action_id:   str,
    name:        str,
    args:        dict,
    business_id: int,
    file_id:     Optional[str] = None,
) -> None:
    _pending_actions[action_id] = {
        "name": name, "args": args,
        "business_id": business_id, "file_id": file_id,
    }


def pop_pending_action(action_id: str) -> Optional[dict]:
    return _pending_actions.pop(action_id, None)
