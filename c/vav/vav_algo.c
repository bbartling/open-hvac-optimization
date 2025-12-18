/*
 * vav_algo.c
 *
 * Implementation of ASHRAE Guideline‑36 zone level request logic for
 * multiple VAV boxes.  This module manages per‑zone timers and
 * hysteresis state required to determine cooling and pressure
 * requests for each VAV box.  The algorithm closely follows the
 * Niagara example provided in the project README but is expressed in
 * C so it can be compiled to WebAssembly using Emscripten.  See
 * vav_algo.h for the public interface.
 */

#include "vav_algo.h"
#include <stdlib.h>

/* Global constants based on ASHRAE Guideline 36.  See the Java
 * implementation in the README for definitions.  These values are
 * intentionally constant so they can be inlined by the compiler.
 */

/* Pressure timing / thresholds (seconds) */
static const double PRESS_PERSIST_SEC      = 60.0; /* 1 minute persistence */
static const double PRESS_RATIO_3REQ       = 0.50;
static const double PRESS_DAMPER_3REQ_MIN  = 95.0;
static const double PRESS_RATIO_2REQ       = 0.70;
static const double PRESS_DAMPER_2REQ_MIN  = 95.0;
static const double PRESS_DAMPER_1REQ_ON   = 95.0;
static const double PRESS_DAMPER_1REQ_OFF  = 85.0;

/* Temperature timing / thresholds */
static const double TEMP_HIGH_DIFF_C  = 3.0;
static const double TEMP_MED_DIFF_C   = 2.0;
static const double TEMP_HIGH_DIFF_F  = 5.0;
static const double TEMP_MED_DIFF_F   = 3.0;
static const double TEMP_PERSIST_SEC  = 120.0; /* 2 minutes */
static const double TEMP_SUPPRESS_SEC = 60.0;  /* 1 minute */
static const double TEMP_LOOP_1REQ_ON  = 95.0;
static const double TEMP_LOOP_1REQ_OFF = 85.0;

/* Internal per‑zone state structure.  One instance of this struct
 * exists for each VAV box.  Timers accumulate elapsed time when
 * conditions are met.  lastPressureReq and lastTempReq store
 * hysteresis state to allow the 1‑request conditions to persist
 * between calls.
 */
typedef struct {
  /* Pressure timers and state */
  double pressHighTimerSec;
  double pressMedTimerSec;
  int    lastPressureReq;
  double lastPressDamperPct;
  double lastPressFlowRatio;
  /* Temperature timers and state */
  double tempHighTimerSec;
  double tempMedTimerSec;
  double tempSuppressTimerSec;
  int    lastTempReq;
  double lastTempDiff;
  double lastTempLoopPct;
} ZoneState;

/* Global pointer to zone state array */
static ZoneState* zoneStates = NULL;
static int zoneCount = 0;

/* Initialise the zone state array.  Any previously allocated state
 * will be freed.  See vav_algo.h for documentation.  */
void vav_init(int n_zones) {
  if (zoneStates) {
    free(zoneStates);
    zoneStates = NULL;
    zoneCount = 0;
  }
  if (n_zones > 0) {
    zoneStates = (ZoneState*)calloc((size_t)n_zones, sizeof(ZoneState));
    zoneCount = n_zones;
    /* calloc zeroes all fields */
  }
}

/* Helper to clamp an integer value between a minimum and maximum. */
static inline int clamp_int(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

/* Compute the pressure request for a single zone.  See the comments
 * above for threshold definitions.  The dt_sec parameter is the
 * elapsed time since the last call.  */
static int compute_pressure(ZoneState* st, double damper, double flow, double flow_sp, double dt_sec) {
  /* If flow setpoint <= 0 treat as invalid and reset timers */
  double ratio = 1.0;
  if (flow_sp > 0.0) {
    ratio = flow / flow_sp;
  } else {
    st->pressHighTimerSec = 0.0;
    st->pressMedTimerSec  = 0.0;
    st->lastPressDamperPct = damper;
    st->lastPressFlowRatio = 0.0;
    st->lastPressureReq = 0;
    return 0;
  }

  st->lastPressDamperPct = damper;
  st->lastPressFlowRatio = ratio;

  /* 3 requests: ratio < 0.50 and damper ≥ 95 for 1 minute */
  int cond3 = (ratio < PRESS_RATIO_3REQ) && (damper >= PRESS_DAMPER_3REQ_MIN);
  if (cond3) {
    st->pressHighTimerSec += dt_sec;
  } else {
    st->pressHighTimerSec = 0.0;
  }
  if (st->pressHighTimerSec >= PRESS_PERSIST_SEC) {
    st->pressMedTimerSec = 0.0;
    st->lastPressureReq = 3;
    return 3;
  }

  /* 2 requests: ratio < 0.70 and damper ≥ 95 for 1 minute */
  int cond2 = (ratio < PRESS_RATIO_2REQ) && (damper >= PRESS_DAMPER_2REQ_MIN);
  if (cond2) {
    st->pressMedTimerSec += dt_sec;
  } else {
    st->pressMedTimerSec = 0.0;
  }
  if (st->pressMedTimerSec >= PRESS_PERSIST_SEC) {
    st->lastPressureReq = 2;
    return 2;
  }

  /* 1 request with hysteresis */
  if (damper >= PRESS_DAMPER_1REQ_ON) {
    st->lastPressureReq = 1;
    return 1;
  }
  if (st->lastPressureReq == 1 && damper >= PRESS_DAMPER_1REQ_OFF) {
    return 1;
  }
  st->lastPressureReq = 0;
  return 0;
}

/* Compute the cooling request for a single zone.  Differences in
 * temperature thresholds and persistence durations between Celsius and
 * Fahrenheit are handled via the is_imperial flag.  */
static int compute_cooling(ZoneState* st, double zoneTemp, double zoneSp,
                           double demand, int is_imperial, double dt_sec) {
  /* Choose thresholds based on unit system */
  double highDiff = is_imperial ? TEMP_HIGH_DIFF_F : TEMP_HIGH_DIFF_C;
  double medDiff  = is_imperial ? TEMP_MED_DIFF_F  : TEMP_MED_DIFF_C;

  double diff = zoneTemp - zoneSp; /* positive means too warm */
  st->lastTempDiff = diff;
  st->lastTempLoopPct = demand;

  /* Advance suppression timer up to its max.  During suppression the
   * zone should not accumulate deviation timers. */
  if (st->tempSuppressTimerSec < TEMP_SUPPRESS_SEC) {
    st->tempSuppressTimerSec += dt_sec;
    if (st->tempSuppressTimerSec > TEMP_SUPPRESS_SEC) {
      st->tempSuppressTimerSec = TEMP_SUPPRESS_SEC;
    }
  }

  if (st->tempSuppressTimerSec >= TEMP_SUPPRESS_SEC) {
    /* Accumulate temperature deviation timers */
    if (diff >= highDiff) {
      st->tempHighTimerSec += dt_sec;
      st->tempMedTimerSec  = 0.0;
    } else if (diff >= medDiff) {
      st->tempMedTimerSec  += dt_sec;
      st->tempHighTimerSec  = 0.0;
    } else {
      st->tempHighTimerSec = 0.0;
      st->tempMedTimerSec  = 0.0;
    }

    if (st->tempHighTimerSec >= TEMP_PERSIST_SEC) {
      st->lastTempReq = 3;
      return 3;
    }
    if (st->tempMedTimerSec >= TEMP_PERSIST_SEC) {
      st->lastTempReq = 2;
      return 2;
    }
  } else {
    /* During suppression period, do not accumulate deviation timers */
    st->tempHighTimerSec = 0.0;
    st->tempMedTimerSec  = 0.0;
  }

  /* 1 request: demand loop saturation with hysteresis */
  if (demand >= TEMP_LOOP_1REQ_ON) {
    st->lastTempReq = 1;
    return 1;
  }
  if (st->lastTempReq == 1 && demand >= TEMP_LOOP_1REQ_OFF) {
    return 1;
  }
  st->lastTempReq = 0;
  return 0;
}

/* Public API: compute requests for all zones.  See header for
 * documentation. */
void vav_update(const double* zoneTemp,
                const double* zoneCoolingSpt,
                const double* zoneDemand,
                const double* vavFlow,
                const double* vavFlowSpt,
                const double* vavDamperCmd,
                double dt_sec,
                int is_imperial,
                int n_zones,
                int* coolRequests,
                int* pressureRequests) {
  if (!zoneStates || n_zones <= 0) return;
  if (n_zones > zoneCount) {
    n_zones = zoneCount;
  }
  for (int i = 0; i < n_zones; ++i) {
    ZoneState* st = &zoneStates[i];
    double t  = zoneTemp[i];
    double sp = zoneCoolingSpt[i];
    double dem = zoneDemand[i];
    double flow = vavFlow[i];
    double flow_sp = vavFlowSpt[i];
    double damper = vavDamperCmd[i];
    /* compute pressure */
    int pReq = compute_pressure(st, damper, flow, flow_sp, dt_sec);
    /* compute cooling */
    int cReq = compute_cooling(st, t, sp, dem, is_imperial, dt_sec);
    /* clamp outputs */
    pressureRequests[i] = clamp_int(pReq, 0, 3);
    coolRequests[i]     = clamp_int(cReq, 0, 3);
  }
}