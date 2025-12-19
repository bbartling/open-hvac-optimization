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
