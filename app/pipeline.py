"""Pipeline: interpret → validate → optimize → build response."""

from __future__ import annotations
from typing import Any

from app.interpreter import interpret_notes
from app.optimizer import optimize
from app.models import ScenarioRequest, OptimizeResponse


async def run_pipeline(request: ScenarioRequest) -> dict[str, Any]:
    """Full pipeline from validated request to response dict."""

    hours_dicts = [h.model_dump() for h in request.hours]
    battery_dict = request.battery.model_dump()
    notes = request.operator_notes

    # ── Step 1: Interpret notes via LLM (async, parallel) ─────────
    raw_directives = await interpret_notes(notes, battery_dict)

    # ── Step 2: Build directive_interpretation response entries ────
    directive_interpretation = []
    validated_directives: list[dict[str, Any]] = []

    for i, raw in enumerate(raw_directives):
        dtype = raw.get("directive_type", "no_op")
        adj = raw.get("structured_adjustment")
        explanation = raw.get("explanation", "")

        applies = dtype != "no_op"

        directive_interpretation.append({
            "note_index": i,
            "applies": applies,
            "directive_type": dtype,
            "structured_adjustment": adj,
            "explanation": explanation,
        })

        if applies:
            validated_directives.append({
                "directive_type": dtype,
                "structured_adjustment": adj,
            })

    # ── Step 3: Run optimizer ─────────────────────────────────────
    hourly_plan = optimize(hours_dicts, battery_dict, validated_directives)

    # ── Step 4: Compute aggregates ────────────────────────────────
    total_grid = sum(h["grid_kwh"] for h in hourly_plan)
    total_cost = sum(
        h["grid_kwh"] * hours_dicts[h["hour"]]["tariff_bdt_per_kwh"]
        for h in hourly_plan
    )
    peak_grid = max(h["grid_kwh"] for h in hourly_plan)

    return {
        "scenario_id": request.scenario_id,
        "directive_interpretation": directive_interpretation,
        "hourly_plan": hourly_plan,
        "total_grid_kwh": round(total_grid, 2),
        "total_cost_bdt": round(total_cost, 2),
        "peak_grid_kwh": round(peak_grid, 2),
        "plan_summary": _build_summary(directive_interpretation, total_cost, peak_grid),
    }


def run_pipeline_with_directives(
    request: ScenarioRequest,
    directives: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pipeline variant that takes pre-built directives (for testing)."""

    hours_dicts = [h.model_dump() for h in request.hours]
    battery_dict = request.battery.model_dump()

    # Build directive_interpretation from given directives
    directive_interpretation = []
    validated_directives: list[dict[str, Any]] = []

    for i, d in enumerate(directives):
        dtype = d.get("directive_type", "no_op")
        adj = d.get("structured_adjustment")
        explanation = d.get("explanation", "")
        applies = dtype != "no_op"

        directive_interpretation.append({
            "note_index": i,
            "applies": applies,
            "directive_type": dtype,
            "structured_adjustment": adj,
            "explanation": explanation,
        })

        if applies:
            validated_directives.append({
                "directive_type": dtype,
                "structured_adjustment": adj,
            })

    hourly_plan = optimize(hours_dicts, battery_dict, validated_directives)

    total_grid = sum(h["grid_kwh"] for h in hourly_plan)
    total_cost = sum(
        h["grid_kwh"] * hours_dicts[h["hour"]]["tariff_bdt_per_kwh"]
        for h in hourly_plan
    )
    peak_grid = max(h["grid_kwh"] for h in hourly_plan)

    return {
        "scenario_id": request.scenario_id,
        "directive_interpretation": directive_interpretation,
        "hourly_plan": hourly_plan,
        "total_grid_kwh": round(total_grid, 2),
        "total_cost_bdt": round(total_cost, 2),
        "peak_grid_kwh": round(peak_grid, 2),
        "plan_summary": _build_summary(directive_interpretation, total_cost, peak_grid),
    }


def _build_summary(
    interp: list[dict], total_cost: float, peak_grid: float
) -> str:
    active = [d for d in interp if d["applies"]]
    if active:
        types = ", ".join(d["directive_type"] for d in active)
        return (
            f"Applied {len(active)} directive(s) ({types}). "
            f"Total grid cost {total_cost:.2f} BDT, peak grid draw {peak_grid:.2f} kWh."
        )
    return (
        f"No active directives. "
        f"Total grid cost {total_cost:.2f} BDT, peak grid draw {peak_grid:.2f} kWh."
    )
