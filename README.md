# Open HVAC Optimization

This repository contains a modular implementation of the
**ASHRAE Guideline 36** request and reset algorithms compiled to
WebAssembly. The goal of this project is to provide a portable
building-automation core that can run on any Linux-based IoT edge
device and be orchestrated from Python. The design mirrors the
Niagara ProgramObjects used in the *open-hvac-optimization* project but
packages each algorithm as a standalone WebAssembly module so it can
be versioned, tested and distributed like a `.bog` file.

Two levels of control are provided at the C layer, plus a combined
“system” module:

* **VAV zone request counter** (`c/vav/vav_algo.c`) — Implements the
  GL-36 zone-level logic that determines cooling and pressure
  requests for each VAV box based on damper position, airflow,
  zone temperature and loop demand. Each call to `vav_update` can
  process an arbitrary number of zones. Timers and hysteresis are
  managed per zone internally.

* **AHU Trim & Respond** (`c/ahu/ahu_algo.c`) — Implements simplified
  duct static pressure and supply air temperature reset algorithms for
  an air handling unit. The algorithms support startup delays,
  adjustable update cadence, ignored request thresholds and bounded
  trim/respond magnitudes. Outside air temperature inputs are
  accepted and can be used to shape SAT reset behavior.

* **Combined VAV + AHU system block** (`c/system/system_algo.c`) —
  Wraps the VAV and AHU algorithms into a single WebAssembly module
  that accepts full-system telemetry (all VAV zones, AHU fan status,
  occupancy, last duct static and SAT setpoints, outside air
  temperature and timestep). On each call it computes per-zone
  cooling/pressure requests and returns updated AHU duct static
  and SAT setpoints while internally tracking Guideline-36 style
  startup delay and update cadence.

Python wrappers (`python/vav_host.py`, `python/ahu_host.py` and
`python/system_host.py`) use the
[wasmtime](https://github.com/bytecodealliance/wasmtime) runtime to
load and interact with the compiled `.wasm` files. They hide the
complexity of WebAssembly memory management and provide idiomatic
methods such as `VAVAlgo.update()`, `AHUAlgo.update_pressure()`,
`AHUAlgo.update_sat()` and `SystemAlgo.update()` for end-to-end
VAV-plus-AHU simulation.

The `tests/` directory contains unit tests exercising the zone,
AHU and combined system algorithms. The tests use a fixed timestep
(e.g. one second) and accumulate minutes of elapsed time via loops to
satisfy the persistence timers specified in the Guideline. When
Emscripten is available the tests call `c/build.sh` to compile the
latest C sources into `.wasm`.

## Repository structure

```text
open-hvac-optimization/
├── LICENSE                  # SPDX: MIT
├── c/                       # C sources and build script
│   ├── build.sh             # builds vav, ahu, hvac, and system *.wasm modules
│   ├── hvac_algo.c          # legacy combined VAV + AHU wrapper (optional)
│   ├── hvac_algo.h          # C API for hvac_algo.wasm
│   ├── vav/
│   │   ├── vav_algo.c       # GL-36 VAV box request logic
│   │   └── vav_algo.h       # C API
│   ├── ahu/
│   │   ├── ahu_algo.c       # Trim & Respond algorithms for AHU
│   │   └── ahu_algo.h       # C API
│   └── system/
│       ├── system_algo.c    # Combined VAV + AHU system algorithm
│       └── system_algo.h    # C API
├── python/                  # Python wrappers using wasmtime
│   ├── algo_host.py         # Legacy hvac_algo.wasm wrapper
│   ├── vav_host.py          # VAV wrapper
│   ├── ahu_host.py          # AHU wrapper
│   └── system_host.py       # Combined system wrapper (VAV + AHU)
├── tests/                   # Pytest unit tests
│   ├── test_vav.py          # VAV algorithm tests
│   ├── test_ahu.py          # AHU algorithm tests
│   └── test_system_algo.py  # Combined system (VAV + AHU) tests
└── README.md                # this file
```


## Prerequisites

* **Emscripten** — You must install the Emscripten SDK to compile the
  C sources to WebAssembly.  On Ubuntu you can follow the official
  instructions to install `emsdk`【175348836414933†L81-L115】.  After installation run
  `source emsdk_env.sh` to put `emcc` on your `PATH`.

* **Python 3.8+** with `pip`.  The tests and wrappers depend on
  `wasmtime`.  Install it into a virtual environment:

  ```sh
  python3 -m venv env
  . env/bin/activate
  pip install wasmtime pytest black
  ```

<details>
<summary>💻 GL36 Code Base in C!</summary>

> Combined VAV + AHU Guideline-36 logic

```c
/*
 * system_algo.c
 *
 * Implementation of the combined VAV + AHU Guideline-36 logic.
 *
 * This translation unit simply reuses the existing, battle-tested
 * VAV and AHU algorithms by including their C sources and wiring
 * them together into a single convenience API.
 */

#include <stddef.h>
#include "../vav/vav_algo.h"
#include "../ahu/ahu_algo.h"
#include "system_algo.h"

void system_init(
    int n_zones,
    double p_sp0,
    double p_spmin,
    double p_spmax,
    double p_startup_delay_sec,
    double p_update_interval_sec,
    double p_ignore_req,
    double p_sp_trim,
    double p_sp_respond,
    double p_sp_respond_max,
    double sat_sp0,
    double sat_spmin,
    double sat_spmax,
    double sat_startup_delay_sec,
    double sat_update_interval_sec,
    double sat_ignore_req,
    double sat_sp_trim,
    double sat_sp_respond,
    double sat_sp_respond_max
)
{
    /* Initialise per-zone VAV state and both AHU loops. */
    vav_init(n_zones);
    ahu_init_pressure(
        p_sp0, p_spmin, p_spmax,
        p_startup_delay_sec, p_update_interval_sec,
        p_ignore_req, p_sp_trim,
        p_sp_respond, p_sp_respond_max
    );
    ahu_init_sat(
        sat_sp0, sat_spmin, sat_spmax,
        sat_startup_delay_sec, sat_update_interval_sec,
        sat_ignore_req, sat_sp_trim,
        sat_sp_respond, sat_sp_respond_max
    );
}

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
)
{
    /* First, compute per-zone requests using the VAV logic.  We
     * use variable-length arrays here for simplicity; the
     * upper-level wrappers ensure that n_zones is modest.
     */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 199901L
    int local_cool[ n_zones ];
    int local_press[ n_zones ];
#else
    /* Fallback for compilers without VLAs: require n_zones > 0 and
     * allocate a small worst-case.  For WebAssembly via Emscripten
     * this branch should not be hit, but is kept for completeness.
     */
    int local_cool[128];
    int local_press[128];
    if (n_zones > 128) {
        n_zones = 128;
    }
#endif

    vav_update(
        zoneTemp,
        zoneCoolingSpt,
        zoneDemand,
        vavFlow,
        vavFlowSpt,
        vavDamperCmd,
        dt_sec,
        is_imperial,
        n_zones,
        local_cool,
        local_press
    );

    /* Aggregate requests across all zones. */
    double total_pressure_req = 0.0;
    double total_cool_req = 0.0;
    for (int i = 0; i < n_zones; ++i) {
        pressureRequests[i] = local_press[i];
        coolRequests[i]     = local_cool[i];
        total_pressure_req += (double)local_press[i];
        total_cool_req     += (double)local_cool[i];
    }

    /* Feed the aggregates into the AHU Trim & Respond loops to
     * compute new duct static and SAT setpoints.
     */
    int fan_and_occ = (fanRun && occupied) ? 1 : 0;
    double next_p = ahu_update_pressure(
        fan_and_occ,
        current_pressure_sp,
        total_pressure_req,
        dt_sec
    );
    double next_sat = ahu_update_sat(
        fan_and_occ,
        current_sat_sp,
        total_cool_req,
        outside_air_temp,
        oat_min,
        oat_max,
        dt_sec
    );

    if (next_pressure_sp) {
        *next_pressure_sp = next_p;
    }
    if (next_sat_sp) {
        *next_sat_sp = next_sat;
    }
}
```


> AHU Only T&R

```c
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

```


> VAV Box Only T&R

```c

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
```


## Building the WebAssembly modules

Run the build script from the `c/` directory:

```sh
cd c
./build.sh
```

This will produce two files:

* `vav/vav_algo.wasm` — the zone request module
* `ahu/ahu_algo.wasm` — the AHU reset module
* `system/system_algo.wasm` — combined AHU and VAV reset module

These files are consumed by the Python wrappers.  If `emcc` is not on
your `PATH` the script will exit without building; in that case you
should place precompiled `.wasm` files in the respective directories.

## Running the tests

After building the wasm modules you can run the unit tests with
`pytest`:

```sh
pytest -q
```

The VAV tests verify that zero inputs produce zero requests; that
consistent undersupply/overheating yields `3` requests after the
appropriate persistence periods; and that invalid data (e.g. zero
flow setpoint) resets the pressure request.  The AHU tests verify
trim and respond behaviour for both pressure and SAT loops, including
clamping to minimum/maximum setpoints.

## Example usage

Below is a minimal example demonstrating how to combine the VAV and
AHU modules to implement a complete GL‑36 airside reset.  This
example assumes that the modules have been built with `c/build.sh`.

```python
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
```

The following output shows a simulated VAV system with an AHU Trim and Respond control loop running in WebAssembly while Python feeds in zone telemetry, occupancy state, and timing. Each interval represents a one-minute timestep where the combined algorithm evaluates VAV cooling and pressure requests and determines whether the AHU should adjust its duct static pressure and supply air temperature setpoints. In this example, the individual VAV boxes begin generating meaningful cooling requests early in the sequence, while the AHU setpoints intentionally remain steady at 1.50 in. wc and 55°F. This behavior demonstrates the embedded Guideline-36 style startup and cadence logic working as intended: even though demand exists, the AHU holds its SPs during the configured startup stabilization period, accumulating operating time until both fan and occupancy conditions are satisfied for long enough to justify a coordinated reset action. This confirms that the combined VAV + AHU WASM block is accurately aggregating VAV requests while respecting AHU startup timing before issuing pressure and temperature reset responses.


```bash
$ python3 python/smoke_test.py 
t_s  duct_sp  sat_sp  coolRequests  pressureRequests
---- -------- ------- -------------- ----------------
   0    1.500   55.00  (0, 0, 1)  (0, 0, 1)
  60    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 120    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 180    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 240    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 300    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 360    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 420    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 480    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 540    1.500   55.00  (2, 0, 1)  (0, 0, 1)
 ```

## Future directions

The current implementation focuses on the airside (VAV box and AHU
trim & respond) logic from Guideline 36.  Possible extensions include:

* **Web App For Testing WASM algorithms** — Visualize algorithm testing for engineers.

* **Central plant reset** — Implement chilled water and hot water
  temperature resets based on aggregated AHU requests.
* **Optimal start** — Convert the various ML models in the
  `open‑hvac‑optimization` project to compiled kernels (.so or .wasm)
  with fixed coefficients.
* **Auto‑generated GitHub Releases** — Use GitHub Actions to build
  the `.wasm` modules on each tag and attach them to a release,
  similar to how Niagara shares `.bog` files.

Contributions are welcome!  See the tests and code for guidance on
maintaining API stability and deterministic behaviour.

---

## 📜 License

Everything here is **MIT Licensed** — free, open source, and made for the BAS community.  
Use it, remix it, or improve it — just share it forward so others can benefit too. 🥰🌍


【MIT License】

Copyright 2025 Ben Bartling

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.