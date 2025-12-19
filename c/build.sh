#!/usr/bin/env bash
# build.sh
#
# Compile the Guideline‑36 VAV and AHU Trim & Respond algorithms into
# standalone WebAssembly modules using Emscripten.  This script
# produces two .wasm files: one for the VAV zone request counter and
# one for the AHU supervisory resets.  The resulting modules export
# only the functions needed by the Python hosts.  Note: Emscripten
# prefixes C function names with an underscore when compiling to
# standalone WASM; the -sEXPORTED_FUNCTIONS list reflects this.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check for emcc
if ! command -v emcc >/dev/null 2>&1; then
  echo "Error: emcc not found on PATH.  Please install Emscripten and source emsdk_env.sh." >&2
  exit 1
fi

echo "Building VAV module..."
emcc vav/vav_algo.c -O3 \
  -sSTANDALONE_WASM=1 \
  -sALLOW_MEMORY_GROWTH=1 \
  -sEXPORTED_FUNCTIONS='["_vav_init","_vav_update","_malloc","_free"]' \
  -o vav/vav_algo.wasm

echo "Building AHU module..."
emcc ahu/ahu_algo.c -O3 \
  -sSTANDALONE_WASM=1 \
  -sALLOW_MEMORY_GROWTH=1 \
  -sEXPORTED_FUNCTIONS='["_ahu_init_pressure","_ahu_update_pressure","_ahu_init_sat","_ahu_update_sat","_malloc","_free"]' \
  -o ahu/ahu_algo.wasm

echo "Build complete: vav/vav_algo.wasm and ahu/ahu_algo.wasm"

echo "Building combined SYSTEM module..."
emcc system/system_algo.c vav/vav_algo.c ahu/ahu_algo.c -O3 \
  -sSTANDALONE_WASM=1 \
  -sALLOW_MEMORY_GROWTH=1 \
  -sEXPORTED_FUNCTIONS='["_system_init","_system_update","_malloc","_free"]' \
  -o system/system_algo.wasm

echo "Build complete: vav/vav_algo.wasm, ahu/ahu_algo.wasm and system/system_algo.wasm"
