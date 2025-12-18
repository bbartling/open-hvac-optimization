/*
 * ahu_algo.c
 *
 * Implementation of simplified Trim & Respond algorithms for AHU duct
 * static pressure and supply air temperature setpoints.  These
 * algorithms are distilled from ASHRAE Guideline‑36 examples and the
 * Niagara reference code.  The goal is not to perfectly reproduce
 * every nuance of the Niagara implementation but to provide a
 * deterministic, portable core that can be compiled to WebAssembly
 * and exercised from Python test cases.  The algorithms support
 * startup delay, update cadence, ignored request thresholds and
 * bounded trim/respond adjustments.  Outside air temperature
 * influence is currently ignored for simplicity.
 */

#include "ahu_algo.h"
#include <stdlib.h>

/* ------------------------------------------------------------------------- */
/* Pressure Trim & Respond internal state */

typedef struct {
  /* Configuration */
  double sp0;
  double spmin;
  double spmax;
  double startup_delay;
  double update_interval;
  double ignore_req;
  double sp_trim;
  double sp_respond;
  double sp_respond_max;
  /* Mutable state */
  double pressure_sp;         /* last commanded setpoint */
  double elapsed_startup;     /* seconds since fan turned on */
  double elapsed_update;      /* seconds since last trim/respond action */
  int fan_on_last;            /* 0/1 flag to detect edges */
} PressureState;

static PressureState pressureState = {0};

void ahu_init_pressure(double sp0, double spmin, double spmax,
                       double startup_delay_sec, double update_interval_sec,
                       double ignore_req, double sp_trim,
                       double sp_respond, double sp_respond_max) {
  pressureState.sp0 = sp0;
  pressureState.spmin = spmin;
  pressureState.spmax = spmax;
  pressureState.startup_delay = startup_delay_sec;
  pressureState.update_interval = update_interval_sec;
  pressureState.ignore_req = ignore_req;
  pressureState.sp_trim = sp_trim;
  pressureState.sp_respond = sp_respond;
  pressureState.sp_respond_max = sp_respond_max;
  /* Reset mutable state */
  pressureState.pressure_sp = sp0;
  pressureState.elapsed_startup = 0.0;
  pressureState.elapsed_update = 0.0;
  pressureState.fan_on_last = 0;
}

static double clamp_double(double v, double lo, double hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

double ahu_update_pressure(int fanRun, double current_sp,
                           double total_requests, double dt_sec) {
  PressureState* st = &pressureState;
  /* Fan off: reset to initial setpoint and clear timers */
  if (!fanRun) {
    st->pressure_sp = st->sp0;
    st->elapsed_startup = 0.0;
    st->elapsed_update  = 0.0;
    st->fan_on_last = 0;
    return st->pressure_sp;
  }
  /* Fan just turned on: edge detection */
  if (st->fan_on_last == 0) {
    st->fan_on_last = 1;
    st->pressure_sp = st->sp0;
    st->elapsed_startup = 0.0;
    st->elapsed_update  = 0.0;
    return st->pressure_sp;
  }
  /* Accumulate startup delay */
  st->elapsed_startup += dt_sec;
  if (st->elapsed_startup < st->startup_delay) {
    /* Hold at sp0 during startup delay */
    st->pressure_sp = st->sp0;
    return st->pressure_sp;
  }
  /* Accumulate update cadence */
  st->elapsed_update += dt_sec;
  if (st->elapsed_update < st->update_interval) {
    /* No change until the cadence is met */
    return st->pressure_sp;
  }
  /* Reset the cadence timer */
  st->elapsed_update = 0.0;
  /* Determine trim or respond action */
  double new_sp;
  if (total_requests <= st->ignore_req) {
    /* Trim: reduce setpoint by a fixed amount */
    new_sp = st->pressure_sp + st->sp_trim;
  } else {
    /* Respond: increase setpoint proportional to requests over the ignore threshold */
    double respond = st->sp_respond * (total_requests - st->ignore_req);
    /* Limit the magnitude of the respond increment */
    if (st->sp_respond > 0) {
      if (respond > st->sp_respond_max) respond = st->sp_respond_max;
    } else {
      if (respond < st->sp_respond_max) respond = st->sp_respond_max;
    }
    new_sp = st->pressure_sp + respond;
  }
  /* Clamp to allowable range and update state */
  st->pressure_sp = clamp_double(new_sp, st->spmin, st->spmax);
  return st->pressure_sp;
}

/* ------------------------------------------------------------------------- */
/* Supply Air Temperature Trim & Respond internal state */

typedef struct {
  /* Configuration */
  double sp0;
  double spmin;
  double spmax;
  double startup_delay;
  double update_interval;
  double ignore_req;
  double sp_trim;
  double sp_respond;
  double sp_respond_max;
  /* Mutable state */
  double sat_sp;
  double elapsed_startup;
  double elapsed_update;
  int fan_on_last;
} SatState;

static SatState satState = {0};

void ahu_init_sat(double sp0, double spmin, double spmax,
                  double startup_delay_sec, double update_interval_sec,
                  double ignore_req, double sp_trim,
                  double sp_respond, double sp_respond_max) {
  satState.sp0 = sp0;
  satState.spmin = spmin;
  satState.spmax = spmax;
  satState.startup_delay = startup_delay_sec;
  satState.update_interval = update_interval_sec;
  satState.ignore_req = ignore_req;
  satState.sp_trim = sp_trim;
  satState.sp_respond = sp_respond;
  satState.sp_respond_max = sp_respond_max;
  satState.sat_sp = sp0;
  satState.elapsed_startup = 0.0;
  satState.elapsed_update = 0.0;
  satState.fan_on_last = 0;
}

double ahu_update_sat(int fanRun, double current_sp,
                      double total_requests, double outside_air_temp,
                      double oat_min, double oat_max, double dt_sec) {
  /* The outside air temperature parameters are accepted for API
   * compatibility but currently unused. */
  (void)outside_air_temp;
  (void)oat_min;
  (void)oat_max;
  SatState* st = &satState;
  /* Fan off: reset to initial setpoint and clear timers */
  if (!fanRun) {
    st->sat_sp = st->sp0;
    st->elapsed_startup = 0.0;
    st->elapsed_update  = 0.0;
    st->fan_on_last = 0;
    return st->sat_sp;
  }
  /* Fan just turned on */
  if (st->fan_on_last == 0) {
    st->fan_on_last = 1;
    st->sat_sp = st->sp0;
    st->elapsed_startup = 0.0;
    st->elapsed_update  = 0.0;
    return st->sat_sp;
  }
  /* Accumulate startup delay */
  st->elapsed_startup += dt_sec;
  if (st->elapsed_startup < st->startup_delay) {
    st->sat_sp = st->sp0;
    return st->sat_sp;
  }
  /* Accumulate update cadence */
  st->elapsed_update += dt_sec;
  if (st->elapsed_update < st->update_interval) {
    return st->sat_sp;
  }
  /* Reset cadence timer */
  st->elapsed_update = 0.0;
  /* Trim or respond */
  double new_sp;
  if (total_requests <= st->ignore_req) {
    /* Trim: raise setpoint (towards warmer) */
    new_sp = st->sat_sp + st->sp_trim;
  } else {
    /* Respond: lower setpoint proportional to requests (cooler) */
    double respond = st->sp_respond * (total_requests - st->ignore_req);
    /* Limit the magnitude of respond */
    if (st->sp_respond > 0) {
      if (respond > st->sp_respond_max) respond = st->sp_respond_max;
    } else {
      if (respond < st->sp_respond_max) respond = st->sp_respond_max;
    }
    new_sp = st->sat_sp + respond;
  }
  st->sat_sp = clamp_double(new_sp, st->spmin, st->spmax);
  return st->sat_sp;
}