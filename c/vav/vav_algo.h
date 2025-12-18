/*
 * vav_algo.h
 *
 * Interface for the Guideline‑36 zone level request logic.  Each VAV box
 * produces pressure and cooling requests based on local temperature,
 * airflow and damper position.  The caller must first call vav_init() to
 * allocate per‑zone state.  Subsequent calls to vav_update() will
 * compute the requests for an arbitrary number of zones.
 *
 * This header is intentionally C‑only so it can be included from C or
 * C++ and compiled to WebAssembly via Emscripten.  Functions take
 * simple types and pointers in order to be FFI friendly.  See
 * python/vav_host.py for a Python wrapper using wasmtime.
 */

#ifndef VAV_ALGO_H
#define VAV_ALGO_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Initialise the internal state for a system of n_zones.  This
 * function must be called before the first call to vav_update().  If
 * called again, any previously allocated state will be freed and
 * reallocated for the new number of zones.
 */
void vav_init(int n_zones);

/*
 * Compute the Guideline‑36 pressure and cooling requests for each VAV
 * zone.  All input arrays must have at least n_zones elements.  The
 * arrays coolRequests and pressureRequests must also have space for
 * n_zones integers.  The dt_sec argument specifies the elapsed time
 * since the previous call.  Set is_imperial to 1 for Fahrenheit
 * temperature thresholds or 0 for Celsius.  The function will clamp
 * requests to the range [0,3].
 */
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
                int* pressureRequests);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* VAV_ALGO_H */