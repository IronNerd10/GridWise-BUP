"""Independent replay validator for GridWise hourly plans.

Checks every constraint listed in the spec:
- Energy balance per hour
- Solar used <= effective solar
- Battery bounds (min/max energy, charge/discharge rates)
- Directive enforcement (no_charge, no_discharge, min reserve, max grid, solar reduction)
- End-of-day neutrality (E[23] == initial_energy)
- Aggregate consistency (total_grid, total_cost, peak_grid)
"""

from __future__ import annotations
import math
from typing import Any

TOL = 0.01  # kWh / BDT tolerance


def validate_plan(
    hours: list[dict[str, Any]],
    battery: dict[str, Any],
    directives: list[dict[str, Any]],
    plan: list[dict[str, Any]],
    response: dict[str, Any],
) -> list[str]:
    """Return a list of violation strings. Empty list = PASS."""

    violations: list[str] = []

    N = 24
    if len(plan) != N:
        violations.append(f"hourly_plan has {len(plan)} entries, expected 24")
        return violations

    # Sort input hours and plan by hour
    hours_sorted = sorted(hours, key=lambda h: h["hour"])
    plan_sorted = sorted(plan, key=lambda p: p["hour"])

    demand = {h["hour"]: h["demand_kwh"] for h in hours_sorted}
    solar = {h["hour"]: h["solar_kwh"] for h in hours_sorted}
    tariff = {h["hour"]: h["tariff_bdt_per_kwh"] for h in hours_sorted}

    cap = battery["capacity_kwh"]
    E0 = battery["initial_energy_kwh"]
    Emin_base = battery["minimum_energy_kwh"]
    max_charge = battery["max_charge_kwh_per_hour"]
    max_discharge = battery["max_discharge_kwh_per_hour"]

    # ── Precompute directive per-hour params ──────────────────────
    eff_sol = dict(solar)
    Ereq = {h: Emin_base for h in range(N)}
    charge_ub = {h: max_charge for h in range(N)}
    discharge_ub = {h: max_discharge for h in range(N)}
    grid_ub_map: dict[int, float] = {}

    solar_red_factors: dict[int, list[float]] = {}

    for d in directives:
        dt = d.get("directive_type", "no_op")
        adj = d.get("structured_adjustment") or {}

        if dt == "no_op":
            continue
        elif dt == "solar_reduction":
            factor = adj["factor"]
            for h in adj["hours"]:
                solar_red_factors.setdefault(h, []).append(factor)
        elif dt == "minimum_battery_reserve":
            min_e = adj["minimum_energy_kwh"]
            for h in adj["hours"]:
                Ereq[h] = max(Ereq[h], min_e)
        elif dt == "no_charge_window":
            for h in adj["hours"]:
                charge_ub[h] = 0.0
        elif dt == "no_discharge_window":
            for h in adj["hours"]:
                discharge_ub[h] = 0.0
        elif dt == "max_grid_window":
            mg = adj["max_grid_kwh"]
            for h in adj["hours"]:
                if h not in grid_ub_map:
                    grid_ub_map[h] = mg
                else:
                    grid_ub_map[h] = min(grid_ub_map[h], mg)

    for h, factors in solar_red_factors.items():
        eff_sol[h] = solar[h] * min(factors)

    # ── Validate each hour ────────────────────────────────────────
    E_prev = E0
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for entry in plan_sorted:
        h = entry["hour"]
        g = entry["grid_kwh"]
        s = entry["solar_used_kwh"]
        action = entry["battery_action"]
        batt_kwh = entry["battery_kwh"]
        E_after = entry["battery_energy_after_kwh"]

        # Determine charge/discharge from action
        if action == "charge":
            c_val = batt_kwh
            x_val = 0.0
        elif action == "discharge":
            c_val = 0.0
            x_val = batt_kwh
        else:  # idle
            c_val = 0.0
            x_val = 0.0
            if abs(batt_kwh) > TOL:
                violations.append(f"h{h}: idle but battery_kwh={batt_kwh}")

        # Energy balance: g + s + x - c == demand
        balance = g + s + x_val - c_val
        if abs(balance - demand[h]) > TOL:
            violations.append(
                f"h{h}: energy balance {balance:.4f} != demand {demand[h]}"
            )

        # Solar used <= effective solar
        if s > eff_sol[h] + TOL:
            violations.append(
                f"h{h}: solar_used {s} > effective solar {eff_sol[h]:.4f}"
            )

        # Grid non-negative
        if g < -TOL:
            violations.append(f"h{h}: grid_kwh {g} is negative")

        # Grid upper bound
        if h in grid_ub_map and g > grid_ub_map[h] + TOL:
            violations.append(
                f"h{h}: grid {g} > max_grid cap {grid_ub_map[h]}"
            )

        # Charge bounds
        if c_val > charge_ub[h] + TOL:
            violations.append(
                f"h{h}: charge {c_val} > charge_ub {charge_ub[h]}"
            )

        # Discharge bounds
        if x_val > discharge_ub[h] + TOL:
            violations.append(
                f"h{h}: discharge {x_val} > discharge_ub {discharge_ub[h]}"
            )

        # Battery transition: E_after = E_prev + c - x
        expected_E = E_prev + c_val - x_val
        if abs(E_after - expected_E) > TOL:
            violations.append(
                f"h{h}: E_after {E_after} != expected {expected_E:.4f} "
                f"(E_prev={E_prev}, c={c_val}, x={x_val})"
            )

        # Battery energy bounds
        if E_after < Ereq[h] - TOL:
            violations.append(
                f"h{h}: E_after {E_after} < minimum {Ereq[h]}"
            )
        if E_after > cap + TOL:
            violations.append(
                f"h{h}: E_after {E_after} > capacity {cap}"
            )

        total_grid += g
        total_cost += g * tariff[h]
        peak_grid = max(peak_grid, g)
        E_prev = E_after

    # ── End-of-day neutrality ─────────────────────────────────────
    final_E = plan_sorted[-1]["battery_energy_after_kwh"]
    if abs(final_E - E0) > TOL:
        violations.append(
            f"End-of-day: E[23]={final_E} != initial {E0}"
        )

    # ── Aggregate checks ──────────────────────────────────────────
    reported_grid = response.get("total_grid_kwh", 0)
    if abs(total_grid - reported_grid) > TOL:
        violations.append(
            f"total_grid_kwh mismatch: computed {total_grid:.2f} vs reported {reported_grid}"
        )

    reported_cost = response.get("total_cost_bdt", 0)
    if abs(total_cost - reported_cost) > TOL:
        violations.append(
            f"total_cost_bdt mismatch: computed {total_cost:.2f} vs reported {reported_cost}"
        )

    reported_peak = response.get("peak_grid_kwh", 0)
    if abs(peak_grid - reported_peak) > TOL:
        violations.append(
            f"peak_grid_kwh mismatch: computed {peak_grid:.2f} vs reported {reported_peak}"
        )

    return violations
