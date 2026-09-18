"""Regex-based fallback extractor for common operator-note patterns.

Used ONLY when the LLM path fails. Output still passes through guardrails.
Covers: solar reduction, no-charge, no-discharge, battery reserve, grid cap.
"""

from __future__ import annotations

import re
from typing import Any


def regex_extract(note: str, battery: dict[str, Any]) -> dict[str, Any] | None:
    """Try to extract a directive from the note using regex patterns.

    Returns a raw directive dict (same shape as LLM output), or None.
    The caller must still run guardrails on the result.
    """
    note_lower = note.lower()

    # ── Extract a time range first ─────────────────────────────────
    hours = _extract_time_range(note)

    # ── Solar reduction ────────────────────────────────────────────
    solar_keywords = ["solar", "panel", "pv", "rooftop", "photovoltaic"]
    if any(kw in note_lower for kw in solar_keywords):
        factor = _extract_solar_factor(note)
        if factor is not None and hours:
            return {
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": hours, "factor": factor},
                "explanation": "Solar reduction detected via regex fallback.",
            }

    # ── No charge window ──────────────────────────────────────────
    no_charge_patterns = [
        r"(?:do\s+not|don'?t|no|stop|suspend|block|isolat)\w*\s+charg",
        r"charg\w+.*?(?:not\s+allowed|forbidden|suspended|blocked|unavailable|disabled|isolated|offline)",
        r"charger\s+(?:will\s+be\s+)?(?:isolated|offline|down|unavailable)",
    ]
    if any(re.search(p, note_lower) for p in no_charge_patterns):
        if hours:
            return {
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "No-charge window detected via regex fallback.",
            }

    # ── No discharge window ───────────────────────────────────────
    no_discharge_patterns = [
        r"(?:do\s+not|don'?t|no|stop|suspend|block)\w*\s+discharg",
        r"discharg\w+\s+(?:is\s+)?(?:not\s+allowed|forbidden|suspended|blocked|disabled)",
        r"(?:must\s+not|cannot|should\s+not)\s+discharg",
        r"(?:do\s+not|don'?t|no)\s+(?:use|drain)\s+(?:the\s+)?batter",
    ]
    if any(re.search(p, note_lower) for p in no_discharge_patterns):
        if hours:
            return {
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "No-discharge window detected via regex fallback.",
            }

    # ── Minimum battery reserve ───────────────────────────────────
    # Match "at least N kWh" or "minimum N kWh" or "keep N kWh"
    reserve_patterns = [
        r"(?:at\s+least|minimum|maintain|keep\s+(?:at\s+least)?)\s+(\d+(?:\.\d+)?)\s*(?:kwh|kWh)",
        r"(?:reserve|hold|store)\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(?:kwh|kWh)",
        r"(\d+(?:\.\d+)?)\s*(?:kwh|kWh)\s+(?:reserve|minimum|in\s+reserve)",
    ]
    for pattern in reserve_patterns:
        m = re.search(pattern, note, re.IGNORECASE)
        if m and hours:
            val = float(m.group(1))
            return {
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {
                    "hours": hours,
                    "minimum_energy_kwh": val,
                },
                "explanation": "Battery reserve detected via regex fallback.",
            }

    # Match "N% of battery capacity"
    pct_reserve = re.search(
        r"(?:at\s+least|keep|maintain)\s+(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:(?:the\s+)?battery\s+)?capacity",
        note,
        re.IGNORECASE,
    )
    if pct_reserve and hours:
        pct = float(pct_reserve.group(1))
        cap = battery.get("capacity_kwh", 0)
        val = pct / 100.0 * cap
        return {
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {
                "hours": hours,
                "minimum_energy_kwh": val,
            },
            "explanation": "Battery reserve (percent) detected via regex fallback.",
        }

    # ── Max grid window ───────────────────────────────────────────
    grid_keywords = ["grid", "import", "draw", "feeder", "utility"]
    if any(kw in note_lower for kw in grid_keywords):
        grid_patterns = [
            r"(?:no\s+more\s+than|under|cap\s+(?:at|of)|max(?:imum)?|limit\w*(?:\s+(?:is|to|at))?|not\s+exceed|stay\s+at\s+or\s+below)\s+(\d+(?:\.\d+)?)\s*(?:kwh|kWh)",
            r"(\d+(?:\.\d+)?)\s*(?:kwh|kWh)\s+(?:cap|limit|max)",
        ]
        for pattern in grid_patterns:
            m = re.search(pattern, note, re.IGNORECASE)
            if m and hours:
                val = float(m.group(1))
                return {
                    "applies": True,
                    "directive_type": "max_grid_window",
                    "structured_adjustment": {
                        "hours": hours,
                        "max_grid_kwh": val,
                    },
                    "explanation": "Grid cap detected via regex fallback.",
                }

    return None


# ── Time parsing helpers ──────────────────────────────────────────


def _parse_time_token(s: str) -> int | None:
    """Convert a time string to an hour 0-23. Returns None on failure."""
    s = s.strip().lower()
    if s in ("noon", "12 noon", "12:00 noon"):
        return 12
    if s in ("midnight", "12 midnight", "12:00 midnight", "12 am", "12:00 am"):
        return 0  # caller handles midnight-as-end

    # HH:MM with optional AM/PM
    m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)?$", s)
    if m:
        h = int(m.group(1))
        ampm = m.group(3)
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return h if 0 <= h <= 23 else None

    # X AM/PM
    m = re.match(r"(\d{1,2})\s*(am|pm)$", s)
    if m:
        h = int(m.group(1))
        ampm = m.group(2)
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return h if 0 <= h <= 23 else None

    # Bare number (24-hour)
    m = re.match(r"(\d{1,2})$", s)
    if m:
        h = int(m.group(1))
        return h if 0 <= h <= 24 else None  # 24 treated as midnight-end

    return None


def _is_midnight_end(token: str) -> bool:
    """Return True if the token represents midnight used as an end-of-range."""
    t = token.strip().lower()
    return t in ("midnight", "12 midnight", "12:00 midnight", "12 am", "12:00 am", "24:00", "24")


def _extract_time_range(note: str) -> list[int] | None:
    """Extract a time range from the note, returning sorted hour list or None."""
    note_clean = note

    # Patterns for "from A to/until B", "between A and B", "A to B"
    range_patterns = [
        # from X to/until Y
        r"from\s+(.+?)\s+(?:to|until|till)\s+(.+?)(?:\s+for\b|\s*[,.]|\s+(?:due|during|because|today|while)|\s*$)",
        # between X and Y
        r"between\s+(.+?)\s+and\s+(.+?)(?:\s+for\b|\s*[,.]|\s+(?:due|during|because|today|while)|\s*$)",
        # X:00-Y:00 or X AM-Y PM style with dash
        r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s*(?:–|-)\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)",
    ]

    for pattern in range_patterns:
        m = re.search(pattern, note_clean, re.IGNORECASE)
        if m:
            start_token = m.group(1).strip()
            end_token = m.group(2).strip()

            # Clean trailing words from tokens
            start_token = re.sub(r"\s+(today|tomorrow|for|due|during).*$", "", start_token, flags=re.IGNORECASE)
            end_token = re.sub(r"\s+(today|tomorrow|for|due|during).*$", "", end_token, flags=re.IGNORECASE)

            start = _parse_time_token(start_token)
            end_val = _parse_time_token(end_token)

            if start is None or end_val is None:
                continue

            # Handle midnight as range end
            if _is_midnight_end(end_token) or end_val == 24:
                end_val = 24

            if end_val == 0 and start > 0:
                # Likely "X PM to midnight" but parsed midnight as 0
                # Check if end_token looks like midnight
                if _is_midnight_end(end_token):
                    end_val = 24

            if start < end_val:
                return list(range(start, min(end_val, 24)))
            elif start > end_val:
                # Wrap around midnight
                return sorted(list(range(0, end_val)) + list(range(start, 24)))

    return None


def _extract_solar_factor(note: str) -> float | None:
    """Extract the solar factor (fraction remaining) from a note."""
    note_lower = note.lower()

    # "drops to X%" / "to X%" / "only X%" / "about X% of normal"
    m = re.search(
        r"(?:drops?\s+to|reduced\s+to|to\s+(?:about\s+)?|only\s+(?:about\s+)?|roughly\s+|approximately\s+)(\d+(?:\.\d+)?)\s*%",
        note_lower,
    )
    if m:
        return float(m.group(1)) / 100.0

    # "reduced BY X%" / "X% reduction" / "drops BY X%"
    m = re.search(
        r"(?:reduced?\s+by|drops?\s+by|loses?\s+|loss\s+of\s+|cut\s+by\s+)(\d+(?:\.\d+)?)\s*%",
        note_lower,
    )
    if m:
        return 1.0 - float(m.group(1)) / 100.0

    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s+(?:reduction|drop|loss|decrease)", note_lower)
    if m:
        return 1.0 - float(m.group(1)) / 100.0

    # "reduced by half" / "half of normal"
    if re.search(r"(?:reduced?\s+by\s+half|half\s+of\s+(?:normal|the\s+forecast)|50\s*%\s+drop)", note_lower):
        return 0.5

    # "a quarter" / "one quarter"
    if re.search(r"(?:a\s+quarter|one\s+quarter|drops?\s+to\s+a\s+quarter)", note_lower):
        return 0.25

    # "a third" / "one third"
    if re.search(r"(?:a\s+third|one\s+third)", note_lower):
        return 1.0 / 3.0

    # "no solar" / "zero output" / "completely offline"
    if re.search(r"(?:no\s+solar|zero\s+(?:output|solar)|completely\s+offline|complete\s+(?:solar\s+)?outage)", note_lower):
        return 0.0

    # Generic "X% of" (without "by")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s+(?:of\s+)?(?:normal|forecast|capacity|output)", note_lower)
    if m:
        return float(m.group(1)) / 100.0

    return None
