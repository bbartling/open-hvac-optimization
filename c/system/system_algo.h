#pragma once

/*
 * system_algo.h
 *
 * High-level combined algorithm that wires together the Guideline-36
 * VAV zone request logic and the AHU Trim & Respond logic into a
 * single WebAssembly module.
 *
 * The intent is to make it easy for an IoT / edge host to feed a
 * whole VAV system (multiple zones plus one AHU) into *one* WASM
 * instance and receive:
 *
 *   - per-zone cooling and pressure requests, and
 *   - updated AHU duct static pressure and supply-air temperature
 *     setpoints.
 *
 * The AHU Trim & Respond loops internally track **time since the
 * fan has been running *while the system is occupied***. Callers
 * pass wall-clock dt_sec and boolean fanRun/occupied flags; the
 * C code integrates startup delay and update cadence exactly like
 * the Java Guideline‑36 reference implementation.
 *
 * The internal implementation reuses the existing vav_algo.c and
 * ahu_algo.c code, so behaviour should match the separate modules.
 *
 * Usage pattern from C/FFI:
 *
 *   system_init(n_zones, ... pressure T&R params ..., ... SAT T&R params ...);
 *   system_update(zoneTemp, zoneCoolingSpt, zoneDemand,
 *                 vavFlow, vavFlowSpt, vavDamperCmd,
 *                 dt_sec, is_imperial, n_zones,
 *                 fanRun,
 *                 current_pressure_sp, current_sat_sp,
 *                 outside_air_temp, oat_min, oat_max,
 *                 coolRequests, pressureRequests,
 *                 &next_pressure_sp, &next_sat_sp);
 *
 * See vav_algo.h and ahu_algo.h for more background on the
 * underlying algorithms.
 */

#ifdef __cplusplus
extern "C" {
#endif

/* Configure the combined algorithm for a given number of zones and
 * Trim & Respond parameters.  This is a thin wrapper that forwards
 * to vav_init(), ahu_init_pressure() and ahu_init_sat().
 */
void system_init(
    int n_zones,
    /* Pressure trim & respond configuration */
    double p_sp0,
    double p_spmin,
    double p_spmax,
    double p_startup_delay_sec,
    double p_update_interval_sec,
    double p_ignore_req,
    double p_sp_trim,
    double p_sp_respond,
    double p_sp_respond_max,
    /* SAT trim & respond configuration */
    double sat_sp0,
    double sat_spmin,
    double sat_spmax,
    double sat_startup_delay_sec,
    double sat_update_interval_sec,
    double sat_ignore_req,
    double sat_sp_trim,
    double sat_sp_respond,
    double sat_sp_respond_max
);

/* Single step of the combined algorithm.
 *
 * Inputs:
 *   zoneTemp[n_zones]        : current zone temperatures
 *   zoneCoolingSpt[n_zones]  : zone cooling setpoints
 *   zoneDemand[n_zones]      : terminal demand (%)
 *   vavFlow[n_zones]         : measured VAV flow
 *   vavFlowSpt[n_zones]      : VAV flow setpoint
 *   vavDamperCmd[n_zones]    : damper command (%)
 *   dt_sec                   : time since previous call (seconds)
 *   is_imperial              : 1 for °F-based thresholds, 0 for °C
 *   n_zones                  : length of all per-zone arrays
 *   fanRun                   : 1 if AHU fan is proven on, else 0
 *   current_pressure_sp      : last commanded duct static SP
 *   current_sat_sp           : last commanded SAT SP
 *   outside_air_temp         : current OAT (passed through to SAT loop)
 *   oat_min, oat_max         : OAT range for any saturation logic
 *
 * Outputs:
 *   coolRequests[n_zones]    : zone cooling requests (0..3)
 *   pressureRequests[n_zones]: zone pressure requests (0..3)
 *   *next_pressure_sp        : updated duct static SP
 *   *next_sat_sp             : updated SAT SP
 */
void system_update(
    const double* zoneTemp,
    const double* zoneCoolingSpt,
    const double* zoneDemand,
    const double* vavFlow,
    const double* vavFlowSpt,
    const double* vavDamperCmd,
    double dt_sec,
    int is_imperial,
    int n_zones,
    int fanRun,
    int occupied,
    double current_pressure_sp,
    double current_sat_sp,
    double outside_air_temp,
    double oat_min,
    double oat_max,
    int* coolRequests,
    int* pressureRequests,
    double* next_pressure_sp,
    double* next_sat_sp
);

#ifdef __cplusplus
} /* extern "C" */
#endif
