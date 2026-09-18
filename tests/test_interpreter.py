#!/usr/bin/env python3
"""Test interpreter against public sample case ground-truth directives.

Runs all operator notes from BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
through the real LLM interpreter and compares directive_type, applies, hours,
and numeric values against the ground truth.

Usage:
    python -m tests.test_interpreter
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.interpreter import interpret_notes, clear_cache

SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
)

TOL = 0.01


def _hours_match(got: dict | None, expected: dict | None) -> bool:
    if got is None and expected is None:
        return True
    if got is None or expected is None:
        return False
    return got.get("hours") == expected.get("hours")


def _value_match(got: dict | None, expected: dict | None, dtype: str) -> bool:
    if got is None and expected is None:
        return True
    if got is None or expected is None:
        return dtype == "no_op"
    if dtype == "solar_reduction":
        return abs(got.get("factor", -1) - expected.get("factor", -2)) < TOL
    if dtype == "minimum_battery_reserve":
        return abs(got.get("minimum_energy_kwh", -1) - expected.get("minimum_energy_kwh", -2)) < TOL
    if dtype == "max_grid_window":
        return abs(got.get("max_grid_kwh", -1) - expected.get("max_grid_kwh", -2)) < TOL
    return True  # no_charge_window, no_discharge_window, no_op


async def main():
    with open(SAMPLE_FILE) as f:
        data = json.load(f)

    cases = data["cases"]

    # Table header
    hdr = f"{'Case':<12} {'Note#':<6} {'Note (first 50 chars)':<52} {'Exp Type':<25} {'Got Type':<25} {'Hours':<6} {'Value':<6} {'Result':<6} {'Latency':<8}"
    print(hdr)
    print("=" * len(hdr))

    total = 0
    passed = 0
    failures = []

    for case in cases:
        case_id = case["id"]
        inp = case["input"]
        expected = case["expected_output"]
        gt_directives = expected["directive_interpretation"]

        battery = inp["battery"]
        notes = inp["operator_notes"]

        clear_cache()

        t0 = time.time()
        results = await interpret_notes(notes, battery)
        elapsed = time.time() - t0

        for i, (got, exp) in enumerate(zip(results, gt_directives)):
            total += 1
            note_text = notes[i][:50]
            exp_type = exp["directive_type"]
            got_type = got.get("directive_type", "???")
            exp_adj = exp.get("structured_adjustment")
            got_adj = got.get("structured_adjustment")

            type_ok = got_type == exp_type
            hrs_ok = _hours_match(got_adj, exp_adj)
            val_ok = _value_match(got_adj, exp_adj, exp_type)

            all_ok = type_ok and hrs_ok and val_ok
            if all_ok:
                passed += 1
                result_str = "PASS"
            else:
                result_str = "FAIL"
                failures.append({
                    "case": case_id,
                    "note_index": i,
                    "note": notes[i],
                    "expected_type": exp_type,
                    "got_type": got_type,
                    "expected_adj": exp_adj,
                    "got_adj": got_adj,
                    "hours_match": hrs_ok,
                    "value_match": val_ok,
                })

            latency_str = f"{elapsed:.2f}s" if i == 0 else ""
            print(
                f"{case_id:<12} {i:<6} {note_text:<52} {exp_type:<25} {got_type:<25} "
                f"{'✓' if hrs_ok else '✗':<6} {'✓' if val_ok else '✗':<6} {result_str:<6} {latency_str:<8}"
            )

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")

    if failures:
        print(f"\n--- FAILURES ---")
        for f in failures:
            print(f"\n  Case {f['case']}, note[{f['note_index']}]: {f['note'][:80]}")
            print(f"    Expected: type={f['expected_type']}, adj={f['expected_adj']}")
            print(f"    Got:      type={f['got_type']}, adj={f['got_adj']}")
            print(f"    Hours match: {f['hours_match']}, Value match: {f['value_match']}")

    return 0 if len(failures) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
