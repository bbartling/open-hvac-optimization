# Open HVAC WASM

This repository contains a modular implementation of the
**ASHRAE Guideline 36** request and reset algorithms compiled to
WebAssembly.  The goal of this project is to provide a portable
building‑automation core that can run on any Linux‑based IoT edge
device and be orchestrated from Python.  The design mirrors the
Niagara ProgramObjects used in the *open‑hvac‑optimization* project but
packages each algorithm as a standalone WebAssembly module so it can
be versioned, tested and distributed like a `.bog` file.

Two levels of control are provided:

* **VAV zone request counter** (`c/vav/vav_algo.c`) — Implements the
  GL‑36 zone‑level logic that determines **cooling** and **pressure**
  requests for each VAV box based on damper position, airflow,
  zone temperature and loop demand.  Each call to `vav_update` can
  process an arbitrary number of zones.  Timers and hysteresis are
  managed per zone internally.

* **AHU Trim & Respond** (`c/ahu/ahu_algo.c`) — Implements simplified
  duct static pressure and supply air temperature reset algorithms for
  an air handling unit.  The algorithms support startup delays,
  adjustable update cadence, ignored request thresholds and bounded
  trim/respond magnitudes.  Outside air temperature inputs are
  accepted but currently ignored.

Python wrappers (`python/vav_host.py` and `python/ahu_host.py`) use
the [wasmtime](https://github.com/bytecodealliance/wasmtime) runtime to
load and interact with the compiled `.wasm` files.  They hide the
complexity of WebAssembly memory management and provide idiomatic
methods such as `VAVAlgo.update()` and `AHUAlgo.update_pressure()`.

The `tests/` directory contains unit tests exercising the zone and
AHU algorithms.  The tests use a one‑second timestep and accumulate
minutes of elapsed time via loops to satisfy the persistence timers
specified in the Guideline.  When Emscripten is available the tests
call `c/build.sh` to compile the latest C sources into `.wasm`.

## Repository structure

```
open-hvac-wasm/
├── LICENSE                  # SPDX: MIT
├── c/                       # C sources and build script
│   ├── build.sh             # compile vav_algo.wasm and ahu_algo.wasm
│   ├── vav/
│   │   ├── vav_algo.c       # GL‑36 VAV box request logic
│   │   └── vav_algo.h       # C API
│   └── ahu/
│       ├── ahu_algo.c       # Trim & Respond algorithms for AHU
│       └── ahu_algo.h       # C API
├── python/                  # Python wrappers using wasmtime
│   ├── vav_host.py          # VAV wrapper
│   └── ahu_host.py          # AHU wrapper
├── tests/                   # Pytest unit tests
│   ├── test_vav.py          # VAV algorithm tests
│   └── test_ahu.py          # AHU algorithm tests
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
  pip install wasmtime pytest
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
from vav_host import VAVAlgo
from ahu_host import AHUAlgo

# Instantiate for 10 zones
vav = VAVAlgo('c/vav/vav_algo.wasm', n_zones=10)
ahu = AHUAlgo('c/ahu/ahu_algo.wasm')

# Initialise AHU loops
ahu.init_pressure(
    sp0=1.5, spmin=1.0, spmax=2.5,
    startup_delay_sec=120.0, update_interval_sec=30.0,
    ignore_req=2.0, sp_trim=-0.1,
    sp_respond=0.2, sp_respond_max=0.4
)
ahu.init_sat(
    sp0=55.0, spmin=50.0, spmax=65.0,
    startup_delay_sec=120.0, update_interval_sec=30.0,
    ignore_req=2.0, sp_trim=0.5,
    sp_respond=-1.0, sp_respond_max=-2.0
)

# At each control interval (e.g. every 10 s) you would:
dt = 10.0
zoneTemps = [...]      # list of floats of length 10
zoneSps = [...]        # list of floats
zoneDemands = [...]    # list of floats
flows = [...]          # list of floats
flowSps = [...]        # list of floats
dampers = [...]         # list of floats

# Compute zone requests
coolReqs, pressReqs = vav.update(zoneTemps, zoneSps, zoneDemands,
                                 flows, flowSps, dampers, dt_sec=dt, is_imperial=0)

# Aggregate requests for AHU
total_press_reqs = sum(pressReqs)
total_cool_reqs  = sum(coolReqs)

# Read current setpoints from your BAS
current_pressure_sp = ...
current_sat_sp      = ...
fan_running = 1

# Update AHU setpoints
new_pressure_sp = ahu.update_pressure(fan_running, current_pressure_sp,
                                      total_press_reqs, dt_sec=dt)
new_sat_sp      = ahu.update_sat(fan_running, current_sat_sp,
                                 total_cool_reqs, outside_air_temp=70.0,
                                 oat_min=50.0, oat_max=80.0, dt_sec=dt)

```

## Future directions

The current implementation focuses on the airside (VAV box and AHU
trim & respond) logic from Guideline 36.  Possible extensions include:

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
