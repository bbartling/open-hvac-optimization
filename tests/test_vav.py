"""
Unit tests for the Guideline‑36 VAV zone request algorithm.  These
tests exercise the WebAssembly module via the Python wrapper in
``python/vav_host.py``.  To keep the test runtime reasonable the
tests use a one‑second timestep and accumulate minutes of elapsed
time by looping.  If Emscripten is installed the build script will
compile the wasm before running the tests; otherwise the tests will
use any existing precompiled modules.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Import the VAV wrapper from the repository
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from vav_host import VAVAlgo  # type: ignore


def build_wasm() -> None:
    """Run the build script if emcc is available.

    If Emscripten is not installed, the wasm file must already exist.
    """
    cdir = Path(__file__).resolve().parents[1] / 'c'
    build_script = cdir / 'build.sh'
    # Only attempt to build if emcc is available
    try:
        subprocess.run(['emcc', '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return
    subprocess.run(['bash', str(build_script)], cwd=str(cdir), check=True)


def test_no_requests():
    """Verify that zero inputs produce zero requests for all zones."""
    build_wasm()
    wasm_path = Path(__file__).resolve().parents[1] / 'c' / 'vav' / 'vav_algo.wasm'
    algo = VAVAlgo(str(wasm_path), n_zones=3)
    zeros = [0.0, 0.0, 0.0]
    ones  = [1.0, 1.0, 1.0]
    cool, press = algo.update(zeros, zeros, zeros, ones, ones, zeros, dt_sec=1.0)
    assert cool == [0, 0, 0]
    assert press == [0, 0, 0]


def test_high_pressure_and_cooling_requests():
    """Zones that stay undersupplied and overheated should eventually request 3."""
    build_wasm()
    wasm_path = Path(__file__).resolve().parents[1] / 'c' / 'vav' / 'vav_algo.wasm'
    n = 2
    algo = VAVAlgo(str(wasm_path), n_zones=n)
    # Constant high demand scenario
    zoneTemp      = [24.0, 24.0]        # zone temperature (°C)
    zoneSp        = [20.0, 20.0]        # cooling setpoint (°C)
    zoneDemand    = [100.0, 100.0]      # loop saturation (%)
    vavFlow       = [0.2, 0.2]          # measured airflow (arbitrary)
    vavFlowSp     = [1.0, 1.0]          # flow setpoint
    vavDamperCmd  = [98.0, 98.0]        # damper command (%)
    # Run for 130 seconds with 1 second timestep.  The algorithm
    # requires 1 minute (pressure) and 2 minutes (temperature) of
    # persistence; after 120 seconds the cooling request should be 3 and
    # after 60 seconds the pressure request should be 3.
    cool = [0] * n
    press = [0] * n
    for t in range(130):
        cool, press = algo.update(zoneTemp, zoneSp, zoneDemand,
                                  vavFlow, vavFlowSp, vavDamperCmd,
                                  dt_sec=1.0, is_imperial=0)
    assert cool == [3, 3]
    assert press == [3, 3]


def test_invalid_flow_setpoint_resets_pressure():
    """If the flow setpoint is zero or negative the pressure request resets to zero."""
    build_wasm()
    wasm_path = Path(__file__).resolve().parents[1] / 'c' / 'vav' / 'vav_algo.wasm'
    algo = VAVAlgo(str(wasm_path), n_zones=1)
    zoneTemp      = [22.0]
    zoneSp        = [20.0]
    zoneDemand    = [90.0]
    vavFlow       = [0.5]
    vavFlowSp     = [0.0]   # invalid setpoint
    vavDamperCmd  = [100.0]
    # After any number of steps, pressure request should remain 0
    for _ in range(10):
        cool, press = algo.update(zoneTemp, zoneSp, zoneDemand,
                                  vavFlow, vavFlowSp, vavDamperCmd,
                                  dt_sec=1.0, is_imperial=0)
        assert press[0] == 0
