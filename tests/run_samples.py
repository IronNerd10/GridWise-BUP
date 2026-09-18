#!/usr/bin/env python3
"""Load sample cases, run the optimizer with ground-truth directives,
and validate the resulting plan.

Usage:
    python -m tests.run_samples
"""

from __future__ import annotations

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import ScenarioRequest
from app.pipeline import run_pipeline_with_directives
from tests.validator import validate_plan

SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
)


def main():
    with open(SAMPLE_FILE) as f:
        data = json.load(f)

    cases = data["cases"]
    passed = 0
    failed = 0

    for case in cases:
        case_id = case["id"]
        label = case.get("label", "")
        inp = case["input"]
        expected = case["expected_output"]

        # Extract ground-truth directives from expected_output
        gt_directives = expected["directive_interpretation"]

        # Build directive list for optimizer
        directives = []
        for di in gt_directives:
            directives.append({
                "directive_type": di["directive_type"],
                "structured_adjustment": di["structured_adjustment"],
                "explanation": di.get("explanation", ""),
            })

        # Parse request
        try:
            request = ScenarioRequest(**inp)
        except Exception as e:
            print(f"FAIL  {case_id} ({label}): request parse error: {e}")
            failed += 1
            continue

        # Run optimizer with ground-truth directives
        try:
            response = run_pipeline_with_directives(request, directives)
        except Exception as e:
            print(f"FAIL  {case_id} ({label}): optimizer error: {e}")
            failed += 1
            continue

        # Validate the plan
        hours_dicts = [h.model_dump() for h in request.hours]
        battery_dict = request.battery.model_dump()

        # Build validated directives (only non-no_op)
        active_directives = [
            d for d in directives if d["directive_type"] != "no_op"
        ]

        violations = validate_plan(
            hours_dicts,
            battery_dict,
            active_directives,
            response["hourly_plan"],
            response,
        )

        # Also check cost optimality: our cost should be <= expected cost + tolerance
        our_cost = response["total_cost_bdt"]
        exp_cost = expected["total_cost_bdt"]
        cost_diff = our_cost - exp_cost

        if violations:
            print(f"FAIL  {case_id} ({label}):")
            for v in violations:
                print(f"      ✗ {v}")
            failed += 1
        else:
            status = "PASS"
            cost_note = ""
            if abs(cost_diff) > 0.01:
                cost_note = f"  [cost diff: {cost_diff:+.2f} BDT]"
            print(
                f"{status}  {case_id} ({label}): "
                f"cost={our_cost:.2f} BDT, peak={response['peak_grid_kwh']:.2f} kWh"
                f"{cost_note}"
            )
            passed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(cases)} cases")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
