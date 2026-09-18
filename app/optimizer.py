"""LP-based energy optimizer using scipy.optimize.linprog (HiGHS)."""

from __future__ import annotations
import math
from typing import Any

import numpy as np
from scipy.optimize import linprog


def optimize(
    hours: list[dict[str, Any]],
    battery: dict[str, Any],
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build and solve the LP, return the 24-entry hourly_plan list.

    Parameters
    ----------
    hours : list[dict]
        Sorted list of 24 hour dicts, each with hour, demand_kwh, solar_kwh,
        tariff_bdt_per_kwh.
    battery : dict
        Battery config: capacity_kwh, initial_energy_kwh, minimum_energy_kwh,
        max_charge_kwh_per_hour, max_discharge_kwh_per_hour.
    directives : list[dict]
        Validated directive dicts (may include no_op which are skipped).

    Returns
    -------
    list[dict]
        24 hourly_plan entries sorted by hour.

    Raises
    ------
    ValueError
        If the LP is infeasible or unbounded.
    """
    N = 24

    # Unpack arrays
    demand = [h["demand_kwh"] for h in hours]
    solar = [h["solar_kwh"] for h in hours]
    tariff = [h["tariff_bdt_per_kwh"] for h in hours]

    cap = battery["capacity_kwh"]
    E0 = battery["initial_energy_kwh"]
    Emin_base = battery["minimum_energy_kwh"]
    max_charge = battery["max_charge_kwh_per_hour"]
    max_discharge = battery["max_discharge_kwh_per_hour"]

    # ── Precompute per-hour directive parameters ──────────────────
    eff_sol = list(solar)  # effective solar
    Ereq = [Emin_base] * N
    charge_ub = [max_charge] * N
    discharge_ub = [max_discharge] * N
    grid_ub = [math.inf] * N

    # Collect minimum solar_reduction factors per hour
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
                grid_ub[h] = min(grid_ub[h], mg)

    # Apply solar reduction: use the MINIMUM factor among all covering
    for h, factors in solar_red_factors.items():
        eff_sol[h] = solar[h] * min(factors)

    # ── Variable layout (120 vars) ────────────────────────────────
    # For each hour h (0..23):
    #   g[h] = idx 5*h + 0   grid
    #   s[h] = idx 5*h + 1   solar used
    #   c[h] = idx 5*h + 2   charge
    #   x[h] = idx 5*h + 3   discharge
    #   E[h] = idx 5*h + 4   battery energy after hour h

    def idx_g(h): return 5 * h + 0
    def idx_s(h): return 5 * h + 1
    def idx_c(h): return 5 * h + 2
    def idx_x(h): return 5 * h + 3
    def idx_E(h): return 5 * h + 4

    n_vars = 5 * N  # 120

    # ── Objective: minimize sum tariff[h] * g[h] ──────────────────
    c_obj = np.zeros(n_vars)
    for h in range(N):
        c_obj[idx_g(h)] = tariff[h]

    # ── Variable bounds ───────────────────────────────────────────
    bounds = [(0, None)] * n_vars  # default: all >= 0

    for h in range(N):
        # g[h] upper bound
        if grid_ub[h] != math.inf:
            bounds[idx_g(h)] = (0, grid_ub[h])
        # s[h] upper bound = effSol[h]
        bounds[idx_s(h)] = (0, eff_sol[h])
        # c[h] upper bound
        bounds[idx_c(h)] = (0, charge_ub[h])
        # x[h] upper bound
        bounds[idx_x(h)] = (0, discharge_ub[h])
        # E[h] bounds: [Ereq[h], cap]
        bounds[idx_E(h)] = (Ereq[h], cap)

    # ── Equality constraints ──────────────────────────────────────
    # 1) Energy balance per hour: g[h] + s[h] + x[h] - c[h] = demand[h]
    # 2) Battery transition:
    #      h=0: E[0] - E0 = c[0] - x[0]  =>  -c[0] + x[0] + E[0] = E0
    #      h>0: E[h] - E[h-1] = c[h] - x[h]  =>  -c[h] + x[h] + E[h] - E[h-1] = 0
    # 3) End-of-day: E[23] = E0  (handled via bounds or explicit eq)

    n_eq = N + N + 1  # 24 balance + 24 transition + 1 end-of-day
    A_eq = np.zeros((n_eq, n_vars))
    b_eq = np.zeros(n_eq)

    row = 0
    # Energy balance
    for h in range(N):
        A_eq[row, idx_g(h)] = 1.0
        A_eq[row, idx_s(h)] = 1.0
        A_eq[row, idx_x(h)] = 1.0
        A_eq[row, idx_c(h)] = -1.0
        b_eq[row] = demand[h]
        row += 1

    # Battery transition
    for h in range(N):
        A_eq[row, idx_c(h)] = -1.0
        A_eq[row, idx_x(h)] = 1.0
        A_eq[row, idx_E(h)] = 1.0
        if h == 0:
            b_eq[row] = E0
        else:
            A_eq[row, idx_E(h - 1)] = -1.0
            b_eq[row] = 0.0
        row += 1

    # End-of-day neutrality: E[23] = E0
    A_eq[row, idx_E(23)] = 1.0
    b_eq[row] = E0
    row += 1

    # ── Solve ─────────────────────────────────────────────────────
    result = linprog(
        c_obj,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        raise ValueError(f"LP solver failed: {result.message}")

    xv = result.x

    # ── Build hourly plan ─────────────────────────────────────────
    plan = []
    for h in range(N):
        g_val = round(xv[idx_g(h)], 4)
        s_val = round(xv[idx_s(h)], 4)
        c_val = round(xv[idx_c(h)], 4)
        x_val = round(xv[idx_x(h)], 4)
        e_val = round(xv[idx_E(h)], 4)

        if c_val > 1e-6:
            action = "charge"
            batt_kwh = c_val
        elif x_val > 1e-6:
            action = "discharge"
            batt_kwh = x_val
        else:
            action = "idle"
            batt_kwh = 0.0

        plan.append({
            "hour": h,
            "grid_kwh": round(g_val, 2),
            "solar_used_kwh": round(s_val, 2),
            "battery_action": action,
            "battery_kwh": round(batt_kwh, 2),
            "battery_energy_after_kwh": round(e_val, 2),
        })

    return plan
