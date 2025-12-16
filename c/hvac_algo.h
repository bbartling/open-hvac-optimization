#pragma once

/*
 * hvac_algo.h
 *
 * This header defines a small API for computing ASHRAE Guideline‑36 zone
 * level requests for an arbitrary number of variable air volume (VAV)
 * boxes. Each VAV produces a cooling (supply air temperature) request
 * and a static pressure request based on its measured temperature,
 * cooling setpoint, terminal demand, airflow and damper position. The
 * implementation lives in hvac_algo.c.
 *
 * To use this library from WebAssembly or native code you must call
 * `hvac_init` exactly once to allocate per‑zone state. Then on each
 * polling cycle call `hvac_update` with arrays of inputs for all
 * zones. The function will update its internal timers and write
 * request counts back into the provided output arrays.
 */

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Initialise the algorithm for a given number of zones. This allocates
 * internal state for each zone. You must call this once before
 * calling hvac_update.
 *
 * Parameters:
 *  n_zones   Number of VAV zones you intend to service.
 */
void hvac_init(int n_zones);

/*
 * Update the zone level request logic for all zones. Each pointer
 * parameter must point to an array of length `n_zones`. The time step
 * `dt_sec` is the elapsed time since the previous call in seconds.
 * If `is_imperial` is non‑zero then inputs/outputs are interpreted
 * using the Fahrenheit thresholds; otherwise Celsius thresholds are
 * used.
 *
 * Inputs:
 *  zoneTemp       – array of zone temperatures (°C or °F)
 *  zoneCoolingSpt – array of zone cooling setpoints (same units)
 *  zoneDemand     – array of cooling demand percentages (0‑100)
 *  vavFlow        – array of measured airflow values
 *  vavFlowSpt     – array of airflow setpoints
 *  vavDamperCmd   – array of damper commands (0‑100 %)
 *  dt_sec         – time since last update in seconds
 *  is_imperial    – 1 for °F/°F thresholds, 0 for °C/°C thresholds
 *  n_zones        – number of entries in each array
 *
 * Outputs:
 *  coolRequests     – array to receive cooling requests (0‑3 per zone)
 *  pressureRequests – array to receive pressure requests (0‑3 per zone)
 */
void hvac_update(const double* zoneTemp,
                 const double* zoneCoolingSpt,
                 const double* zoneDemand,
                 const double* vavFlow,
                 const double* vavFlowSpt,
                 const double* vavDamperCmd,
                 double dt_sec,
                 int is_imperial,
                 int n_zones,
                 int* coolRequests,
                 int* pressureRequests);

#ifdef __cplusplus
}
#endif