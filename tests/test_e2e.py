#!/usr/bin/env python3
"""End-to-end test: full pipeline through FastAPI (real LLM interpreter).

For each public sample case:
  1. POST to /optimize-energy via httpx ASGITransport (in-process)
  2. Validate the plan using GROUND-TRUTH directives from the sample file
  3. Compare cost to the reference

Usage:
    python -m tests.test_e2e
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pyrefly: ignore [missing-import]
import httpx

from app.main import app
from app.interpreter import clear_cache
from tests.validator import validate_plan

SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
)

TOL = 0.01


async def main():
    with open(SAMPLE_FILE) as f:
        data = json.load(f)

    cases = data["cases"]

    hdr = f"{'Case':<12} {'Label':<35} {'HTTP':<5} {'Violations':<12} {'Our Cost':<12} {'Ref Cost':<12} {'Diff':<10} {'Result':<6}"
    print(hdr)
    print("=" * len(hdr))

    passed = 0
    failed = 0
    clear_cache()

    transport = httpx.ASGITransport(app=app)  # type: ignore
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for case in cases:
            case_id = case["id"]
            label = case.get("label", "")[:33]
            inp = case["input"]
            expected = case["expected_output"]

            resp = await client.post("/optimize-energy", json=inp)
            status = resp.status_code

            if status != 200:
                print(f"{case_id:<12} {label:<35} {status:<5} {'N/A':<12} {'N/A':<12} {'N/A':<12} {'N/A':<10} FAIL")
                failed += 1
                continue

            result = resp.json()

            # Extract ground-truth directives (non-no_op only)
            gt_directives = [
                d for d in expected["directive_interpretation"]
                if d["directive_type"] != "no_op"
            ]

            # Validate the plan against GROUND-TRUTH directives
            hours_dicts = sorted(inp["hours"], key=lambda h: h["hour"])
            violations = validate_plan(
                hours_dicts,
                inp["battery"],
                gt_directives,
                result["hourly_plan"],
                result,
            )

            our_cost = result.get("total_cost_bdt", 0)
            ref_cost = expected["total_cost_bdt"]
            cost_diff = our_cost - ref_cost

            if violations:
                print(
                    f"{case_id:<12} {label:<35} {status:<5} {len(violations):<12} "
                    f"{our_cost:<12.2f} {ref_cost:<12.2f} {cost_diff:<+10.2f} FAIL"
                )
                for v in violations[:3]:
                    print(f"      ✗ {v}")
                failed += 1
            else:
                print(
                    f"{case_id:<12} {label:<35} {status:<5} {'0':<12} "
                    f"{our_cost:<12.2f} {ref_cost:<12.2f} {cost_diff:<+10.2f} PASS"
                )
                passed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(cases)} cases")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
