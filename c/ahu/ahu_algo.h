/*
 * ahu_algo.h
 *
 * Interfaces for ASHRAE Guideline‑36 Trim & Respond algorithms at the
 * air handling unit (AHU) level.  These functions reset either the
 * duct static pressure setpoint or the supply air temperature setpoint
 * based on aggregated zone requests.  They are designed to be
 * compiled to WebAssembly via Emscripten and invoked from Python.
 *
 * The pressure and temperature reset logic are implemented
 * independently so they can be used together or separately.  Each
 * algorithm maintains internal timers and remembers the last setpoint
 * between calls.  The user must initialise each control loop via
 * ahu_init_pressure() and/or ahu_init_sat() before calling the
 * corresponding update function.
 */

#ifndef AHU_ALGO_H
#define AHU_ALGO_H

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------- Pressure Trim & Respond ------------------------ */

/* Initialise the duct static pressure reset algorithm.  All durations
 * are specified in seconds.  The control logic will start at sp0 and
 * clamp subsequent outputs between spmin and spmax.  The
 * startup_delay_sec defines how long the fan must run before any
 * trim/respond actions occur.  The update_interval_sec sets the
 * minimum interval between successive adjustments.  When the total
 * requests from zones (R) is less than or equal to ignore_req the
 * setpoint will be reduced by sp_trim each update (trim).  When R is
 * greater than ignore_req the setpoint will be increased by
 * sp_respond multiplied by (R − ignore_req), limited to
 * sp_respond_max on a single update.  All values should be given in
 * the same units (e.g. Pa or in. w.c.).
 */
void ahu_init_pressure(double sp0, double spmin, double spmax,
                       double startup_delay_sec, double update_interval_sec,
                       double ignore_req, double sp_trim,
                       double sp_respond, double sp_respond_max);

/* Update the pressure setpoint.  The caller must pass a flag
 * indicating whether the fan is currently running (fanRun=1) or not
 * (fanRun=0).  The current_sp parameter should be the most recently
 * commanded setpoint.  total_requests is the sum of the zone pressure
 * requests.  dt_sec is the elapsed time since the previous call.
 * Returns the new setpoint to use.  If the fan is off the algorithm
 * will drive the setpoint to sp0 immediately and wait for the fan to
 * stabilise before resuming control.  If either the startup delay
 * window or the update interval has not expired the function will
 * return the previous setpoint.
 */
double ahu_update_pressure(int fanRun, double current_sp,
                           double total_requests, double dt_sec);

/* ---------------------- Supply Air Temperature Trim & Respond ------------- */

/* Initialise the SAT trim & respond algorithm.  sp0, spmin and spmax
 * define the initial, minimum and maximum allowable supply air
 * temperatures (e.g. °C or °F).  startup_delay_sec and
 * update_interval_sec behave as described in ahu_init_pressure().
 * ignore_req, sp_trim, sp_respond and sp_respond_max control the
 * magnitude of the trim and respond actions.  When total requests
 * exceed ignore_req the setpoint will be decreased by sp_respond
 * multiplied by (R − ignore_req), limited by sp_respond_max per
 * update.  When R is less than or equal to ignore_req the setpoint
 * will be increased by sp_trim.  Note: for cooling SAT reset the
 * respond increment (sp_respond) should be negative and the trim
 * increment (sp_trim) positive.
 */
void ahu_init_sat(double sp0, double spmin, double spmax,
                  double startup_delay_sec, double update_interval_sec,
                  double ignore_req, double sp_trim,
                  double sp_respond, double sp_respond_max);

/* Update the SAT setpoint.  fanRun and dt_sec behave as in
 * ahu_update_pressure().  current_sp is the current SAT setpoint and
 * total_requests is the total number of zone cooling requests.  The
 * outside_air_temp, oat_min and oat_max parameters are reserved for
 * future enhancements; the current implementation ignores them but
 * accepts them for API stability.  Returns the new SAT setpoint.
 */
double ahu_update_sat(int fanRun, double current_sp,
                      double total_requests, double outside_air_temp,
                      double oat_min, double oat_max, double dt_sec);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* AHU_ALGO_H */