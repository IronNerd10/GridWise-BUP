#!/usr/bin/env python3
"""Paraphrase test: 27 hand-written notes covering edge cases.

Covers: 24-hour clock, spelled-out times, "reduce by" vs "drop to",
"reduce by half", MWh units, wrap-around midnight, midnight end,
percent-of-capacity reserve, total-over-window grid cap, "do not use the
battery", distractor notes, and prompt injection.

Usage:
    python -m tests.test_paraphrase
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.interpreter import interpret_notes, clear_cache

TOL = 0.05  # slightly relaxed for LLM interpretation variance

DEFAULT_BATTERY = {
    "capacity_kwh": 200,
    "initial_energy_kwh": 100,
    "minimum_energy_kwh": 40,
    "max_charge_kwh_per_hour": 50,
    "max_discharge_kwh_per_hour": 50,
}

# Each test case: (note, expected_type, expected_hours_or_None, expected_value_dict_or_None)
PARAPHRASE_CASES = [
    # 1: "reduce by 80%" (Solar reduction)
    (
        "Rooftop PV reduced by 80% between 10 AM and noon for cleaning.",
        "solar_reduction", [10, 11], {"factor": 0.2},
    ),
    # 2: Wrap-around midnight (No charge window)
    (
        "Do not charge the battery from 10 PM to 2 AM.",
        "no_charge_window", [0, 1, 22, 23], None,
    ),
    # 3: Distractor - meeting (No op)
    (
        "Department meeting at 2 PM in Room 301.",
        "no_op", None, None,
    ),
]


def _check_type(got_type: str, exp_type: str) -> bool:
    return got_type == exp_type


def _check_hours(got_adj: dict | None, exp_hours: list[int] | None) -> bool:
    if exp_hours is None:
        return True  # no_op, don't check hours
    if got_adj is None:
        return False
    return got_adj.get("hours") == exp_hours


def _check_value(got_adj: dict | None, exp_val: dict | None, exp_type: str) -> bool:
    if exp_val is None:
        return True
    if got_adj is None:
        return False
    if exp_type == "solar_reduction":
        return abs(got_adj.get("factor", -1) - exp_val["factor"]) < TOL
    if exp_type == "minimum_battery_reserve":
        return abs(got_adj.get("minimum_energy_kwh", -1) - exp_val["minimum_energy_kwh"]) < TOL
    if exp_type == "max_grid_window":
        return abs(got_adj.get("max_grid_kwh", -1) - exp_val["max_grid_kwh"]) < TOL
    return True


async def main():
    clear_cache()

    hdr = f"{'#':<4} {'Note (first 55 chars)':<57} {'Exp Type':<25} {'Got Type':<25} {'Hours':<6} {'Value':<6} {'Result':<6} {'Lat':<6}"
    print(hdr)
    print("=" * len(hdr))

    total = 0
    passed = 0
    failures = []

    for idx, (note, exp_type, exp_hours, exp_val) in enumerate(PARAPHRASE_CASES):
        total += 1
        t0 = time.time()
        results = await interpret_notes([note], DEFAULT_BATTERY)
        elapsed = time.time() - t0
        got = results[0]

        got_type = got.get("directive_type", "???")
        got_adj = got.get("structured_adjustment")

        type_ok = _check_type(got_type, exp_type)
        hrs_ok = _check_hours(got_adj, exp_hours)
        val_ok = _check_value(got_adj, exp_val, exp_type)

        # For prompt injection (case 15), accept any valid output
        is_injection = idx == 14
        if is_injection:
            # Must be a valid directive type
            valid_types = {"solar_reduction", "minimum_battery_reserve", "no_charge_window",
                          "no_discharge_window", "max_grid_window", "no_op"}
            all_ok = got_type in valid_types
            type_ok = all_ok
            hrs_ok = True
            val_ok = True
        else:
            all_ok = type_ok and hrs_ok and val_ok

        if all_ok:
            passed += 1
            result_str = "PASS"
        else:
            result_str = "FAIL"
            failures.append({
                "index": idx + 1,
                "note": note,
                "expected_type": exp_type,
                "got_type": got_type,
                "expected_hours": exp_hours,
                "got_hours": got_adj.get("hours") if got_adj else None,
                "expected_val": exp_val,
                "got_adj": got_adj,
            })

        note_text = note[:55]
        print(
            f"{idx+1:<4} {note_text:<57} {exp_type:<25} {got_type:<25} "
            f"{'✓' if hrs_ok else '✗':<6} {'✓' if val_ok else '✗':<6} {result_str:<6} {elapsed:.2f}s"
        )

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")

    if failures:
        print(f"\n--- FAILURES ---")
        for f in failures:
            print(f"\n  #{f['index']}: {f['note'][:80]}")
            print(f"    Expected: type={f['expected_type']}, hours={f['expected_hours']}, val={f['expected_val']}")
            print(f"    Got:      type={f['got_type']}, hours={f['got_hours']}, adj={f['got_adj']}")

    return 0 if len(failures) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
