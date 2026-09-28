#!/usr/bin/env python3
"""
AI risk reviewer for trader-agent.

Returns a structured ALLOW/DENY verdict for a trade signal. The backend is
selected by environment, so switching providers is a .env edit plus a restart:

  CLOUD_API_URL set   -> OpenAI-compatible chat completions (DeepSeek, Groq, OpenRouter, ...)
  CLOUD_API_URL unset -> local Ollama

Every call is bounded by AI_TIMEOUT_SECONDS. Every response is parsed strictly.
This module never guesses a verdict and never decides the fail policy; that
belongs to the caller.
"""

import json
import os
import re
import time

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
CLOUD_API_URL = os.getenv("CLOUD_API_URL", "")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY", "")
CLOUD_API_MODEL = os.getenv("CLOUD_API_MODEL", "deepseek-chat")
AI_MODEL = os.getenv("AI_MODEL", "phi3:mini")

AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "15"))
AI_MAX_PREDICT = int(os.getenv("AI_MAX_PREDICT", "96"))

_JSON_INSTRUCTION = (
    'Reply with ONLY valid JSON, no prose and no code fence: '
    '{"decision":"ALLOW"|"DENY","confidence":0.0-1.0,"reason":"brief"}. '
    "decision must be exactly ALLOW or DENY."
)


class AiUnavailable(Exception):
    """Transport error, timeout, or a response with no usable decision object."""


def backend() -> str:
    return "cloud" if CLOUD_API_URL else "ollama"


def decide(prompt: str) -> dict:
    """
    Return {"decision", "confidence", "reason", "latency_ms", "source"}.

    Raises AiUnavailable when the provider cannot be reached, times out, or
    returns something that does not contain a valid ALLOW/DENY object.
    """
    started = time.monotonic()
    try:
        raw = _call_cloud(prompt) if CLOUD_API_URL else _call_ollama(prompt)
    except AiUnavailable:
        raise
    except Exception as exc:
        raise AiUnavailable(f"{type(exc).__name__}: {exc}") from exc

    result = _parse(raw)
    if result is None:
        raise AiUnavailable("response contained no parseable decision object")
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    result["source"] = backend()
    return result


def _call_ollama(prompt: str) -> str:
    import httpx

    with httpx.Client(timeout=AI_TIMEOUT_SECONDS) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": AI_MODEL,
                "messages": [{"role": "user", "content": f"{prompt}\n\n{_JSON_INSTRUCTION}"}],
                "stream": False,
                "keep_alive": -1,
                "options": {
                    "temperature": 0,
                    "num_predict": AI_MAX_PREDICT,
                    "num_ctx": 2048,
                },
            },
        )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_cloud(prompt: str) -> str:
    import httpx

    with httpx.Client(timeout=AI_TIMEOUT_SECONDS) as client:
        resp = client.post(
            CLOUD_API_URL,
            headers={"Authorization": f"Bearer {CLOUD_API_KEY}"},
            json={
                "model": CLOUD_API_MODEL,
                "messages": [{"role": "user", "content": f"{prompt}\n\n{_JSON_INSTRUCTION}"}],
                "temperature": 0,
                "max_tokens": AI_MAX_PREDICT,
            },
        )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse(text: str):
    """
    Extract the decision object. Prefers the whole response, so an outer object
    wins over any nested one; falls back to scanning for embedded objects.
    None if there is no usable decision.
    """
    text = (text or "").strip()

    parsed = _from_text(text)
    if parsed is not None:
        return parsed

    for candidate in re.findall(r"\{[^{}]*\}", text, re.DOTALL):
        parsed = _from_text(candidate)
        if parsed is not None:
            return parsed
    return None


def _from_text(text: str):
    try:
        # Small models emit leading-zero numbers such as 00.4
        fixed = re.sub(r":\s*0+(\d)", lambda m: f": {m.group(1)}", text)
        parsed = json.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict) or "decision" not in parsed:
        return None
    decision = str(parsed.get("decision", "")).upper()
    if decision not in ("ALLOW", "DENY"):
        return None
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "decision": decision,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(parsed.get("reason", "no reason provided"))[:280],
    }
