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