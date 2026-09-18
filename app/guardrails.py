"""Deterministic guardrail validator for LLM directive responses.

Parses raw LLM text, validates structure, fixes applies/structured_adjustment,
converts percent_of_capacity, and strips extraneous keys.
Returns a validated dict or None (caller should retry/fallback).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def validate_and_fix(raw_text: str, battery: dict[str, Any]) -> dict[str, Any] | None:
    """Parse and validate an LLM response string.

    Returns a dict with keys: directive_type, structured_adjustment, explanation.
    Returns None if the response is irrecoverably invalid.
    """

    # Step 1: Strip markdown code fences, then json.loads
    text = raw_text.strip()
    # Remove ```json ... ``` or ``` ... ```
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    m = fence_pattern.match(text)
    if m:
        text = m.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    # Step 2: directive_type must be one of six allowed values
    dtype = data.get("directive_type")
    if dtype not in ALLOWED_TYPES:
        return None

    # Step 3: Force applies and structured_adjustment for no_op
    if dtype == "no_op":
        return {
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": _coerce_explanation(data.get("explanation")),
        }

    # From here on, dtype is a non-no_op type.
    # Step 4: structured_adjustment must be a dict with valid hours
    adj = data.get("structured_adjustment")
    if not isinstance(adj, dict):
        return None

    hours_raw = adj.get("hours")
    if not isinstance(hours_raw, list) or len(hours_raw) == 0:
        return None

    clean_hours: list[int] = []
    for h in hours_raw:
        if isinstance(h, float):
            if h != int(h):
                return None  # fractional hour -> invalid
            h = int(h)
        if not isinstance(h, int):
            return None
        if h < 0 or h > 23:
            return None  # out of range -> invalid
        clean_hours.append(h)

    # Dedupe and sort ascending
    clean_hours = sorted(set(clean_hours))
    if len(clean_hours) == 0:
        return None

    # Steps 5–8: type-specific validation and key stripping
    cap = battery.get("capacity_kwh", 0)

    if dtype == "solar_reduction":
        factor = adj.get("factor")
        if not isinstance(factor, (int, float)):
            return None
        factor = float(factor)
        if not math.isfinite(factor) or factor < 0 or factor > 1:
            return None
        new_adj: dict[str, Any] = {"hours": clean_hours, "factor": factor}

    elif dtype == "minimum_battery_reserve":
        min_e = adj.get("minimum_energy_kwh")
        pct = adj.get("percent_of_capacity")

        # Step 6: convert percent_of_capacity if needed
        if min_e is None and pct is not None:
            if not isinstance(pct, (int, float)) or not math.isfinite(float(pct)):
                return None
            min_e = float(pct) / 100.0 * cap

        if min_e is None:
            return None
        if not isinstance(min_e, (int, float)):
            return None
        min_e = float(min_e)
        if not math.isfinite(min_e) or min_e < 0:
            return None
        if min_e > cap:
            min_e = cap  # clamp to capacity

        new_adj = {"hours": clean_hours, "minimum_energy_kwh": min_e}

    elif dtype == "no_charge_window":
        new_adj = {"hours": clean_hours}

    elif dtype == "no_discharge_window":
        new_adj = {"hours": clean_hours}

    elif dtype == "max_grid_window":
        mg = adj.get("max_grid_kwh")
        if not isinstance(mg, (int, float)):
            return None
        mg = float(mg)
        if not math.isfinite(mg) or mg < 0:
            return None
        new_adj = {"hours": clean_hours, "max_grid_kwh": mg}

    else:
        return None  # unreachable but safe

    # Step 9: explanation
    explanation = _coerce_explanation(data.get("explanation"))

    return {
        "directive_type": dtype,
        "structured_adjustment": new_adj,
        "explanation": explanation,
    }


def _coerce_explanation(val: Any) -> str:
    if isinstance(val, str) and val.strip():
        return val.strip()[:200]
    return "Directive applied per operator note."
