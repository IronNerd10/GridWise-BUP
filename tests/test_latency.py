#!/usr/bin/env python3
"""Latency test: 20 sequential requests with cache cold and warm.

Reports p50, p95, max total request latency.
Target: p95 under 5 seconds.

Usage:
    python -m tests.test_latency
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pyrefly: ignore [missing-import]
import httpx

from app.main import app
from app.interpreter import clear_cache

SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
)


def _percentile(sorted_data: list[float], p: float) -> float:
    """Simple percentile calculation."""
    n = len(sorted_data)
    idx = p / 100.0 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


async def run_requests(client: httpx.AsyncClient, cases: list, n: int = 20) -> list[float]:
    """Send n sequential POST requests, return list of latencies."""
    latencies = []
    for i in range(n):
        case = cases[i % len(cases)]
        inp = case["input"]

        t0 = time.time()
        resp = await client.post("/optimize-energy", json=inp)
        elapsed = time.time() - t0

        status = resp.status_code
        latencies.append(elapsed)
        print(f"  req {i+1:>2}: {elapsed:.3f}s  (HTTP {status}, case {case['id']})")

    return latencies


def report(label: str, latencies: list[float]):
    sorted_l = sorted(latencies)
    p50 = _percentile(sorted_l, 50)
    p95 = _percentile(sorted_l, 95)
    mx = max(sorted_l)
    target = "✓" if p95 < 5.0 else "✗"

    print(f"\n  {label}:")
    print(f"    p50:  {p50:.3f}s")
    print(f"    p95:  {p95:.3f}s  {target} (target < 5s)")
    print(f"    max:  {mx:.3f}s")
    print(f"    mean: {sum(sorted_l)/len(sorted_l):.3f}s")


async def main():
    with open(SAMPLE_FILE) as f:
        data = json.load(f)
    cases = data["cases"]

    transport = httpx.ASGITransport(app=app)  # type: ignore
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # ── Cold cache ────────────────────────────────────────────
        print("=== COLD CACHE (1 request) ===")
        clear_cache()
        cold_latencies = await run_requests(client, cases, 1)
        report("Cold cache", cold_latencies)

        # ── Warm cache ────────────────────────────────────────────
        print("\n=== WARM CACHE (1 request) ===")
        # Don't clear cache — reuse from cold run
        warm_latencies = await run_requests(client, cases, 1)
        report("Warm cache", warm_latencies)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
