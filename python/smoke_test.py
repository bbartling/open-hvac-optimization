from __future__ import annotations

from pathlib import Path

from system_host import SystemAlgo, SystemConfig


def main() -> None:
    base = Path(__file__).resolve().parents[1]
    wasm_path = base / "c" / "system" / "system_algo.wasm"

    cfg = SystemConfig(
        n_zones=3,
        # Pressure loop config
        p_sp0=1.5, p_spmin=1.0, p_spmax=3.0,
        p_startup_delay_sec=900.0,
        p_update_interval_sec=300.0,
        p_ignore_req=0.2,
        p_sp_trim=-0.05,
        p_sp_respond=0.10,
        p_sp_respond_max=0.25,
        # SAT loop config
        sat_sp0=55.0, sat_spmin=50.0, sat_spmax=65.0,
        sat_startup_delay_sec=900.0,
        sat_update_interval_sec=300.0,
        sat_ignore_req=0.2,
        sat_sp_trim=0.5,
        sat_sp_respond=-1.0,
        sat_sp_respond_max=-2.0,
    )

    algo = SystemAlgo(str(wasm_path), cfg)

    zoneTemp       = [75.0, 74.0, 73.0]
    zoneCoolingSpt = [72.0, 72.0, 72.0]
    zoneDemand     = [50.0, 80.0, 95.0]
    vavFlow        = [800.0, 900.0, 950.0]
    vavFlowSpt     = [1000.0, 1000.0, 1000.0]
    vavDamperCmd   = [60.0, 80.0, 95.0]

    duct_sp = 1.5
    sat_sp  = 55.0
    oat     = 65.0
    dt      = 60.0

    print("t_s  duct_sp  sat_sp  coolRequests  pressureRequests")
    print("---- -------- ------- -------------- ----------------")

    t = 0.0
    for _ in range(10):
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
            occupied=1,
            current_pressure_sp=duct_sp,
            current_sat_sp=sat_sp,
            outside_air_temp=oat,
            oat_min=40.0,
            oat_max=90.0,
        )
        print(f"{t:4.0f} {duct_sp:8.3f} {sat_sp:7.2f}  {cool}  {press}")
        t += dt


if __name__ == "__main__":
    main()
