#!/bin/bash
# build.sh
#
# Simple build script for compiling the HVAC Guideline‑36 request logic
# library to a WebAssembly module using Emscripten. This script
# requires that the emcc compiler from the Emscripten SDK is available
# on your PATH. The resulting hvac_algo.wasm will contain two
# exported functions:
#   - hvac_init
#   - hvac_update
# You can call these from Python via a WASM runtime such as wasmtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR"

# Check for emcc
if ! command -v emcc >/dev/null 2>&1; then
  echo "Error: emcc not found on PATH. Please install the Emscripten SDK and source emsdk_env.sh." >&2
  exit 1
fi

cd "$SCRIPT_DIR"
SRC="hvac_algo.c"
OUT="hvac_algo.wasm"

# Compile the C code to WebAssembly. We use STANDALONE_WASM to
# produce a minimal wasm module without a JS harness. We also
# explicitly export our two public functions along with malloc/free
# so that host languages can allocate memory.

emcc "$SRC" -O3 \
  -s STANDALONE_WASM=1 \
  -s EXPORTED_FUNCTIONS='["_hvac_init","_hvac_update","_malloc","_free"]' \
  -s EXPORTED_RUNTIME_METHODS='["cwrap","malloc","free"]' \
  -o "$OUT"

echo "Build complete: $OUT"