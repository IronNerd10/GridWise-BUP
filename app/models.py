"""Pydantic v2 request/response models for GridWise."""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Request models ────────────────────────────────────────────────

class HourEntry(BaseModel):
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float


class Battery(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
        mode="after",
    )
    @classmethod
    def non_negative(cls, v: float, info) -> float:
        if v < 0:
            raise ValueError(f"{info.field_name} must be non-negative")
        return v

    @model_validator(mode="after")
    def battery_invariants(self):
        if self.capacity_kwh < self.minimum_energy_kwh:
            raise ValueError("capacity_kwh must be >= minimum_energy_kwh")
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError(
                "initial_energy_kwh must be between minimum_energy_kwh and capacity_kwh"
            )
        return self


class ScenarioRequest(BaseModel):
    scenario_id: str
    operator_notes: list[str]
    hours: list[HourEntry]
    battery: Battery

    @field_validator("operator_notes", mode="after")
    @classmethod
    def validate_notes(cls, v: list[str]) -> list[str]:
        if not (1 <= len(v) <= 3):
            raise ValueError("operator_notes must have 1 to 3 entries")
        for i, note in enumerate(v):
            if not note.strip():
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
        return v

    @field_validator("hours", mode="after")
    @classmethod
    def validate_hours(cls, v: list[HourEntry]) -> list[HourEntry]:
        if len(v) != 24:
            raise ValueError("hours must have exactly 24 entries")
        seen = set()
        for entry in v:
            if entry.hour < 0 or entry.hour > 23:
                raise ValueError(f"hour value {entry.hour} out of range 0..23")
            if entry.hour in seen:
                raise ValueError(f"duplicate hour {entry.hour}")
            seen.add(entry.hour)
        # Sort by hour internally
        v.sort(key=lambda e: e.hour)
        return v


# ─── Response models ───────────────────────────────────────────────

class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: Optional[dict[str, Any]] = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
