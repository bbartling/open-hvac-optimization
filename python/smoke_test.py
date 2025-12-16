"""
smoke_test.py
================

This script exercises the HVAC Guideline‑36 WebAssembly module using the
`HVACAlgo` wrapper defined in `algo_host.py`. It constructs an
algorithm instance for a configurable number of zones, feeds it some
dummy telemetry and prints the resulting cooling and pressure
requests. The numbers used here are arbitrary but demonstrate the
persistence and hysteresis behaviour described in Guideline 36.

Run this script after compiling the C code to WebAssembly with
`c/build.sh` and installing the `wasmtime` Python package.
"""

from __future__ import annotations

import time
from pathlib import Path
from algo_host import HVACAlgo


def main() -> None:
    # Determine path to wasm (assumes script is run from python/)
    wasm_path = Path(__file__).resolve().parent.parent / "c" / "hvac_algo.wasm"
    n_zones = 2
    dt = 10.0  # seconds between updates

    algo = HVACAlgo(str(wasm_path), n_zones)

    # Example telemetry for two zones
    # Each entry in the list corresponds to one zone at a given time step
    zoneTemp        = [23.0, 21.0]  # °C initial temperatures
    zoneCoolingSpt  = [22.0, 22.0]
    zoneDemand      = [50.0, 20.0]
    vavFlow         = [500.0, 300.0]
    vavFlowSpt      = [600.0, 400.0]
    vavDamperCmd    = [80.0, 60.0]

    print("Testing HVAC Guideline‑36 algorithm with 2 zones")
    for step in range(0, 20):
        # Simulate some dynamics: gradually increase damper on zone 0
        vavDamperCmd[0] = min(100.0, vavDamperCmd[0] + 2.0)
        # Zone temp drifts up on zone 1
        zoneTemp[1] += 0.1
        # Update algorithm
        cool, press = algo.update(
            zoneTemp,
            zoneCoolingSpt,
            zoneDemand,
            vavFlow,
            vavFlowSpt,
            vavDamperCmd,
            dt,
            is_imperial=False,
        )
        print(f"t={step*dt:>4.0f}s  CoolRequests={cool}  PressureRequests={press}")
        # Optionally wait to simulate real time
        # time.sleep(0.5)


if __name__ == "__main__":
    main()