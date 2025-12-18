"""
Unit tests for the Guideline‑36 AHU Trim & Respond algorithms.  These
tests exercise both the duct static pressure reset and supply air
temperature reset logic compiled to WebAssembly and wrapped by
``python/ahu_host.py``.  The tests use simplified parameters and
short update intervals to verify trim/respond behaviour, startup
delay and fan state handling.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import the AHU wrapper from the repository
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from ahu_host import AHUAlgo  # type: ignore


def build_wasm() -> None:
    """Run the build script if emcc is available."""
    cdir = Path(__file__).resolve().parents[1] / 'c'
    build_script = cdir / 'build.sh'
    try:
        subprocess.run(['emcc', '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return
    subprocess.run(['bash', str(build_script)], cwd=str(cdir), check=True)


def test_pressure_trim_and_respond():
    """Pressure setpoint should trim and respond according to total requests."""
    build_wasm()
    wasm_path = Path(__file__).resolve().parents[1] / 'c' / 'ahu' / 'ahu_algo.wasm'
    algo = AHUAlgo(str(wasm_path))
    # Configure pressure loop: no startup delay, 1s update cadence
    algo.init_pressure(
        sp0=1.5, spmin=1.0, spmax=2.5,
        startup_delay_sec=0.0, update_interval_sec=1.0,
        ignore_req=1.0,
        sp_trim=-0.1, sp_respond=0.2, sp_respond_max=0.5
    )
    # Start with fan off → should always return sp0
    sp = algo.update_pressure(fanRun=0, current_sp=1.5, total_requests=0.0, dt_sec=1.0)
    assert sp == 1.5
    # Fan on: first call resets to sp0
    sp = algo.update_pressure(fanRun=1, current_sp=1.5, total_requests=0.0, dt_sec=1.0)
    assert sp == 1.5
    # With total_requests <= ignore_req, setpoint should trim by sp_trim each second
    sp = algo.update_pressure(fanRun=1, current_sp=sp, total_requests=0.5, dt_sec=1.0)
    assert abs(sp - 1.4) < 1e-6
    sp = algo.update_pressure(fanRun=1, current_sp=sp, total_requests=0.5, dt_sec=1.0)
    assert abs(sp - 1.3) < 1e-6
    # With total_requests > ignore_req, respond proportional to excess, limited by sp_respond_max
    # (R - ignore) = (3.0 - 1.0) = 2.0 → respond = 0.2 * 2 = 0.4 (within max 0.5)
    sp = algo.update_pressure(fanRun=1, current_sp=sp, total_requests=3.0, dt_sec=1.0)
    # Expect 1.3 + 0.4 = 1.7
    assert abs(sp - 1.7) < 1e-6
    # If (R - ignore) produces respond > max, cap at sp_respond_max (=0.5)
    sp = algo.update_pressure(fanRun=1, current_sp=sp, total_requests=6.0, dt_sec=1.0)
    # (6-1)*0.2=1.0 → limited to 0.5 → 1.7+0.5=2.2
    assert abs(sp - 2.2) < 1e-6
    # Ensure clamp to spmax
    sp = algo.update_pressure(fanRun=1, current_sp=sp, total_requests=50.0, dt_sec=1.0)
    # (50-1)*0.2=9.8 -> limited to 0.5 -> 2.2+0.5=2.7 but clamp to 2.5
    assert abs(sp - 2.5) < 1e-6


def test_sat_trim_and_respond():
    """SAT setpoint should warm (trim) or cool (respond) accordingly."""
    build_wasm()
    wasm_path = Path(__file__).resolve().parents[1] / 'c' / 'ahu' / 'ahu_algo.wasm'
    algo = AHUAlgo(str(wasm_path))
    # Configure SAT loop: no startup delay, 1s update cadence
    algo.init_sat(
        sp0=55.0, spmin=50.0, spmax=65.0,
        startup_delay_sec=0.0, update_interval_sec=1.0,
        ignore_req=1.0,
        sp_trim=1.0,   # warm by 1°F when requests below threshold
        sp_respond=-2.0,  # cool by 2°F per request above threshold
        sp_respond_max=-3.0
    )
    # Fan off → returns sp0
    sp = algo.update_sat(fanRun=0, current_sp=55.0, total_requests=0.0,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    assert sp == 55.0
    # Fan on first call resets to sp0
    sp = algo.update_sat(fanRun=1, current_sp=55.0, total_requests=0.0,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    assert sp == 55.0
    # total_requests <= ignore → warm by +1.0 each second
    sp = algo.update_sat(fanRun=1, current_sp=sp, total_requests=0.5,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    assert abs(sp - 56.0) < 1e-6
    sp = algo.update_sat(fanRun=1, current_sp=sp, total_requests=0.5,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    assert abs(sp - 57.0) < 1e-6
    # total_requests > ignore → cool proportional to excess but limited to sp_respond_max
    # (R - 1) = 2.0 → respond = -2*2=-4 → limited to -3
    sp = algo.update_sat(fanRun=1, current_sp=sp, total_requests=3.0,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    # 57 + (-3) = 54
    assert abs(sp - 54.0) < 1e-6
    # clamp to spmin if necessary
    sp = algo.update_sat(fanRun=1, current_sp=sp, total_requests=30.0,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    # (R - 1) = 29 → respond = -58 → limited to -3 → 54 + (-3) = 51 → above spmin
    assert abs(sp - 51.0) < 1e-6
    # more cooling; should clamp to spmin=50
    sp = algo.update_sat(fanRun=1, current_sp=sp, total_requests=30.0,
                         outside_air_temp=70.0, oat_min=50.0, oat_max=80.0,
                         dt_sec=1.0)
    # 51 + (-3) = 48, but clamp to 50
    assert abs(sp - 50.0) < 1e-6
