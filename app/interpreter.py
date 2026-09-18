"""LLM-backed interpreter for operator notes.

Calls the configured LLM (default: Groq/llama-3.3-70b-versatile) for each note
in parallel, validates via guardrails, retries once, then falls back to regex
and finally to no_op.

TODO: To swap LLM providers, update .env with LLM_BASE_URL and LLM_MODEL.
      Any OpenAI-compatible chat completions API works.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import httpx

from app.prompt import SYSTEM_PROMPT
from app.guardrails import validate_and_fix
from app.fallback_rules import regex_extract

# ── Configuration (from env with defaults) ────────────────────────

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT_SECONDS", "8"))

# ── In-memory cache: note text -> validated directive dict ────────

_cache: dict[str, dict[str, Any]] = {}


def clear_cache() -> None:
    """Clear the interpreter cache (for testing)."""
    _cache.clear()


def get_cache() -> dict[str, dict[str, Any]]:
    """Return the cache dict (for testing)."""
    return _cache


# ── Public API ────────────────────────────────────────────────────


async def interpret_notes(
    notes: list[str],
    battery: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert operator notes into directive dicts (async, parallel).

    Parameters
    ----------
    notes : list[str]
        The raw operator_notes from the request (1-3 strings).
    battery : dict
        The battery config dict (capacity_kwh, etc.).

    Returns
    -------
    list[dict]
        One directive dict per note, in the same order.
    """
    tasks = [
        _interpret_single(note, idx, battery) for idx, note in enumerate(notes)
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


# ── Internal helpers ──────────────────────────────────────────────


async def _interpret_single(
    note: str, index: int, battery: dict[str, Any]
) -> dict[str, Any]:
    """Interpret a single note with LLM -> retry -> regex -> no_op fallback."""

    # Check cache
    if note in _cache:
        print(f"  note[{index}]: cache_hit")
        return _cache[note]

    # Attempt 1: LLM
    result = await _try_llm(note, battery)
    if result is not None:
        print(f"  note[{index}]: llm")
        _cache[note] = result
        return result

    # Attempt 2: LLM retry
    result = await _try_llm(note, battery)
    if result is not None:
        print(f"  note[{index}]: llm_retry")
        _cache[note] = result
        return result

    # Attempt 3: Regex fallback
    raw_regex = regex_extract(note, battery)
    if raw_regex is not None:
        # Build a JSON string from the regex output so guardrails can validate
        regex_text = json.dumps(raw_regex)
        validated = validate_and_fix(regex_text, battery)
        if validated is not None:
            print(f"  note[{index}]: regex_fallback")
            _cache[note] = validated
            return validated

    # Attempt 4: no_op fallback
    print(f"  note[{index}]: no_op_fallback")
    fallback = {
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "Could not interpret note; treating as no-op.",
    }
    _cache[note] = fallback
    return fallback


async def _try_llm(
    note: str, battery: dict[str, Any]
) -> dict[str, Any] | None:
    """Make one LLM call, validate with guardrails. Return dict or None."""

    if not LLM_API_KEY:
        return None

    try:
        model_to_use = LLM_MODEL
        if model_to_use == "llama-3.3-70b-versatile" and LLM_API_KEY.startswith("gsk_"):
            model_to_use = "openai/gpt-oss-120b"

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
                    resp = await client.post(
                        f"{LLM_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {LLM_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model_to_use,
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": f"NOTE: {note}"},
                            ],
                            "temperature": 0,
                            "max_tokens": 300,
                            "response_format": {"type": "json_object"},
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return validate_and_fix(content, battery)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                print(f"  LLM HTTP {e.response.status_code}: {e.response.text}")
                return None
            except httpx.TimeoutException:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                print(f"  LLM timeout for note: {note[:60]}...")
                return None
    except Exception as e:
        print(f"  LLM error: {type(e).__name__}")
        return None
