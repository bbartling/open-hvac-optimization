# HVAC Guideline‑36 WebAssembly Module

This repository demonstrates how to implement the ASHRAE Guideline 36
zone‑level request logic in C, compile it to a WebAssembly module
using Emscripten and then call it from Python. The same pattern
generalises to any IoT edge application where Python acts as the
“gateway glue” (handling BACnet, MQTT and scheduling) while
performance‑sensitive algorithms live in compiled code. By shipping
site‑specific logic as a `.wasm` blob you can change behaviour
without modifying the gateway itself—similar to how Niagara shares
`.bog` or `.jar` files across platforms.

## Directory layout

```text
hvac_wasm_algo/
├── c/
│   ├── hvac_algo.c       # C implementation of the G36 request logic
│   ├── hvac_algo.h       # Public API
│   └── build.sh          # Build script to compile the C code to WebAssembly
├── python/
│   ├── algo_host.py      # Python wrapper around the WebAssembly module
│   └── smoke_test.py     # Example script exercising the algorithm
└── README.md             # You are here
```

## Prerequisites

To build and run this example you will need:

1. **Emscripten SDK** – This provides the `emcc` compiler. Follow the
   [official installation guide](https://emscripten.org/docs/getting_started/downloads.html)
   or install via your package manager on Linux. Once installed,
   source the `emsdk_env.sh` script so that `emcc` is on your `PATH`.
2. **Python 3.8+**
3. **wasmtime** – Python bindings for the Wasmtime WebAssembly
   runtime. Install via `pip install wasmtime`.

These instructions assume a Linux/WSL environment but should also
apply to macOS.

## Building the WebAssembly module

From within the `c` directory run the provided build script:

```bash
cd c
./build.sh
```

If configured correctly this will produce a `hvac_algo.wasm` file in
the same directory. The script exports the `hvac_init` and
`hvac_update` functions along with `malloc` and `free` for memory
management. You can adjust the `-O3` optimisation level and exported
functions as needed.

## Running the smoke test

After building, you can run the Python smoke test to verify that the
module works end‑to‑end:

```bash
cd python
pip install wasmtime  # if not already installed
python smoke_test.py
```

This will construct an `HVACAlgo` wrapper around the compiled
WebAssembly module, initialise it for two zones and then feed in
sample telemetry every 10 seconds. The script prints the cooling and
pressure requests for each zone at each time step. You can modify
`n_zones`, the inputs and the time step to suit your own needs.

## Integrating with a real IoT gateway

In a production setting your Python gateway would call
`hvac_init(n_zones)` once during start‑up and then call
`hvac_update()` every polling period (e.g. 10 seconds) with the latest
telemetry. The gateway would convert BACnet point values into the
simple arrays expected by the algorithm and then publish the returned
request counts to MQTT or write them back to BACnet. Because the
algorithm state is internal to the WebAssembly module, there is no
shared mutable state in Python—making the gateway code much easier to
reason about.

## Note on data validity

For brevity this example does not replicate all of the data
sanitisation and fail‑safe logic found in the original Niagara
implementation (e.g. checking for invalid point values and clearing
timers on error). If you require full parity, you can add additional
checks in `hvac_algo.c` before computing requests.

## License

This example is provided under the MIT license. See the `LICENSE`
file for details.