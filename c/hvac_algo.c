#include "hvac_algo.h"
#include <stdlib.h>
#include <string.h>

/*
 * Implementation of ASHRAE Guideline‑36 zone level request logic.
 * This module manages per‑zone timers and hysteresis state required to
 * determine cooling and pressure requests for a VAV box. The algorithm
 * follows the logic found in the Niagara example provided by the user
 * but is expressed in C so that it can be compiled to a WebAssembly
 * module using Emscripten.
 *
 * For more information see the README in the root of this project.
 */

/* Global constants based on ASHRAE Guideline 36. See the Java code
 * provided in the prompt for definitions. */

/* Execution period is not baked into this module; dt_sec is passed in
 * on each call to hvac_update. */

/* Pressure timing / thresholds (seconds) */
static const double PRESS_PERSIST_SEC = 60.0; /* 1 minute persistence */
static const double PRESS_RATIO_3REQ  = 0.50;
static const double PRESS_DAMPER_3REQ_MIN = 95.0;
static const double PRESS_RATIO_2REQ  = 0.70;
static const double PRESS_DAMPER_2REQ_MIN = 95.0;
static const double PRESS_DAMPER_1REQ_ON  = 95.0;
static const double PRESS_DAMPER_1REQ_OFF = 85.0;

/* Temperature timing / thresholds */
static const double TEMP_HIGH_DIFF_C = 3.0;
static const double TEMP_MED_DIFF_C  = 2.0;
static const double TEMP_HIGH_DIFF_F = 5.0;
static const double TEMP_MED_DIFF_F  = 3.0;
static const double TEMP_PERSIST_SEC  = 120.0; /* 2 minutes */
static const double TEMP_SUPPRESS_SEC = 60.0;  /* 1 minute */
static const double TEMP_LOOP_1REQ_ON  = 95.0;
static const double TEMP_LOOP_1REQ_OFF = 85.0;

/* Internal per‑zone state */
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

void hvac_init(int n_zones) {
  if (zoneStates) {
    free(zoneStates);
    zoneStates = NULL;
    zoneCount = 0;
  }
  if (n_zones > 0) {
    zoneStates = (ZoneState*)calloc((size_t)n_zones, sizeof(ZoneState));
    zoneCount = n_zones;
    /* initialisation of values happens via calloc (zeroed) */
  }
}

static inline int clamp_int(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

/* Compute pressure request for one zone */
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

  /* 3 requests: ratio < 0.50 and damper >= 95 for 1 minute */
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

  /* 2 requests: ratio < 0.70 and damper >= 95 for 1 minute */
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

/* Compute cooling request for one zone */
static int compute_cooling(ZoneState* st, double zoneTemp, double zoneSp, double demand,
                           int is_imperial, double dt_sec) {
  /* Temperature difference thresholds based on unit system */
  double highDiff = is_imperial ? TEMP_HIGH_DIFF_F : TEMP_HIGH_DIFF_C;
  double medDiff  = is_imperial ? TEMP_MED_DIFF_F  : TEMP_MED_DIFF_C;

  double diff = zoneTemp - zoneSp; /* positive means too warm */
  st->lastTempDiff = diff;
  st->lastTempLoopPct = demand;

  /* advance suppression timer up to its max */
  if (st->tempSuppressTimerSec < TEMP_SUPPRESS_SEC) {
    st->tempSuppressTimerSec += dt_sec;
    if (st->tempSuppressTimerSec > TEMP_SUPPRESS_SEC) {
      st->tempSuppressTimerSec = TEMP_SUPPRESS_SEC;
    }
  }

  if (st->tempSuppressTimerSec >= TEMP_SUPPRESS_SEC) {
    /* accumulate temperature deviation timers */
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
    /* during suppression period, do not accumulate */
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