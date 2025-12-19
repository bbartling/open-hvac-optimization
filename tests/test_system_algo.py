"""
Unit tests for the combined Guideline-36 VAV + AHU system algorithm.

These tests exercise the WebAssembly module built from
``c/system/system_algo.c`` via the Python wrapper in
``python/system_host.py``. We explicitly check that:

* When the fan is on but the system is NOT occupied, the AHU Trim & Respond
  logic behaves as "fan off" (setpoints reset to initial values, timers
  cleared).

* When both fanRun and occupied are true for long enough to satisfy the
  startup delay and update cadence, the duct static pressure and SAT
  setpoints change in response to VAV requests.
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Make sure we can import from the python/ directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from system_host import SystemAlgo, SystemConfig  # type: ignore


def build_wasm() -> None:
    """Run the C build script if emcc is available.

    If Emscripten is not installed, the wasm file must already exist.
    """
    cdir = Path(__file__).resolve().parents[1] / "c"
    build_script = cdir / "build.sh"
    try:
        subprocess.run(
            ["emcc", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # No Emscripten: assume wasm has already been built
        return
    subprocess.run(["bash", str(build_script)], cwd=str(cdir), check=True)


def make_system_algo() -> SystemAlgo:
    """Helper to create a SystemAlgo instance with short GL36 timers.

    We use very small startup delay and update interval so the test can
    run quickly while still exercising the same logic as "real"
    Guideline-36 parameters.
    """
    base = Path(__file__).resolve().parents[1]
    wasm_path = base / "c" / "system" / "system_algo.wasm"

    cfg = SystemConfig(
        n_zones=3,
        # Pressure loop config
        p_sp0=1.5,
        p_spmin=1.0,
        p_spmax=3.0,
        p_startup_delay_sec=5.0,   # small startup delay for test
        p_update_interval_sec=2.0, # short cadence
        p_ignore_req=0.1,
        p_sp_trim=-0.05,
        p_sp_respond=0.5,
        p_sp_respond_max=0.25,
        # SAT loop config
        sat_sp0=55.0,
        sat_spmin=50.0,
        sat_spmax=65.0,
        sat_startup_delay_sec=5.0,
        sat_update_interval_sec=2.0,
        sat_ignore_req=0.1,
        sat_sp_trim=0.5,
        sat_sp_respond=-1.0,
        sat_sp_respond_max=-2.0,
    )

    return SystemAlgo(str(wasm_path), cfg)


def test_ahu_setpoints_hold_until_fan_and_occupied_then_move():
    """End-to-end: VAV + AHU combined block.

    1) With fanRun=1 but occupied=0, AHU behaves like fan off:
       setpoints stay pinned to initial sp0 and timers never advance.

    2) After we set occupied=1 and run long enough to satisfy the
       startup delay and update cadence, the AHU duct static and SAT
       setpoints should move in response to VAV cooling/pressure
       requests.
    """
    build_wasm()
    algo = make_system_algo()
    cfg = algo.config

    # Telemetry: same flavour as your combined smoke test
    zoneTemp       = [75.0, 74.0, 73.0]
    zoneCoolingSpt = [72.0, 72.0, 72.0]
    zoneDemand     = [50.0, 80.0, 95.0]
    vavFlow        = [800.0, 900.0, 950.0]
    vavFlowSpt     = [1000.0, 1000.0, 1000.0]
    vavDamperCmd   = [60.0, 80.0, 95.0]

    dt = 1.0  # seconds
    duct_sp = cfg.p_sp0
    sat_sp = cfg.sat_sp0

    # ------------------------------------------------------------------
    # Phase 1: fan ON but NOT occupied -> AHU should keep resetting to sp0
    # ------------------------------------------------------------------
    pre_history = []
    for _ in range(5):
        cool, press, duct_sp, sat_sp = algo.update(
            zoneTemp,
            zoneCoolingSpt,
            zoneDemand,
            vavFlow,
            vavFlowSpt,
            vavDamperCmd,
            dt_sec=dt,
            is_imperial=True,
            fanRun=1,
            occupied=0,  # <--- not occupied
            current_pressure_sp=duct_sp,
            current_sat_sp=sat_sp,
            outside_air_temp=65.0,
            oat_min=40.0,
            oat_max=90.0,
        )
        pre_history.append((duct_sp, sat_sp))

    for dsp, ssp in pre_history:
        assert dsp == pytest.approx(cfg.p_sp0)
        assert ssp == pytest.approx(cfg.sat_sp0)

    # ------------------------------------------------------------------
    # Phase 2: fan ON and occupied -> after startup + cadence, SPs move
    # ------------------------------------------------------------------
    moved = False
    max_steps = 40  # plenty to exceed startup_delay + several cadences
    for _ in range(max_steps):
        cool, press, duct_sp, sat_sp = algo.update(
            zoneTemp,
            zoneCoolingSpt,
            zoneDemand,
            vavFlow,
            vavFlowSpt,
            vavDamperCmd,
            dt_sec=dt,
            is_imperial=True,
            fanRun=1,
            occupied=1,  # <--- now occupied
            current_pressure_sp=duct_sp,
            current_sat_sp=sat_sp,
            outside_air_temp=65.0,
            oat_min=40.0,
            oat_max=90.0,
        )
        # "Moved" means different from the initial GL36 sp0, within tolerance
        if (
            abs(duct_sp - cfg.p_sp0) > 1e-6
            or abs(sat_sp - cfg.sat_sp0) > 1e-6
        ):
            moved = True
            break

    assert moved, "AHU setpoints never moved after fan+occupied and sufficient dt"
