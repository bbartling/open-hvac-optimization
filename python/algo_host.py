"""
algo_host.py
================

This module demonstrates how to load and call the compiled HVAC
Guideline‑36 request logic from Python via a WebAssembly runtime. It
uses the `wasmtime` Python bindings (https://pypi.org/project/wasmtime/) to
instantiate the `hvac_algo.wasm` module produced by Emscripten.

The HVACAlgo class wraps the raw WebAssembly functions and provides
convenience methods for initialising the algorithm state and computing
cooling and pressure requests for multiple VAV zones at once. The
Python arrays you pass are automatically marshalled into the module's
linear memory and the results are copied back into Python memory.

Note: You must compile the C source into WebAssembly using the
provided `build.sh` before attempting to use this module. You will
also need to install the `wasmtime` package in your Python
environment. See README.md for full instructions.
"""

from __future__ import annotations

import ctypes
from typing import Sequence, Tuple

try:
    from wasmtime import Store, Module, Instance, Memory
except ImportError as exc:
    raise ImportError(
        "wasmtime module is required. Install it with 'pip install wasmtime'."
    ) from exc


class HVACAlgo:
    """Wrapper around the compiled hvac algorithm WebAssembly module."""

    def __init__(self, wasm_path: str, n_zones: int):
        # Load module and set up store
        self.store = Store()
        self.module = Module.from_file(self.store.engine, wasm_path)
        # Create an empty import list (no WASI support required)
        self.instance = Instance(self.store, self.module, [])
        # Exports
        exports = self.instance.exports(self.store)
        self.mem: Memory = exports["memory"]  # linear memory
        # Functions exported by Emscripten are prefixed with underscore
        self._init = exports["hvac_init"]
        self._update = exports["hvac_update"]
        # Malloc/free for allocating buffers
        self._malloc = exports.get("malloc")
        self._free = exports.get("free")
        # Remember zone count
        self.n_zones = n_zones
        # Call init once to allocate internal state inside the module
        self._init(self.store, n_zones)

    def _alloc(self, length: int, itemsize: int) -> int:
        """Allocate a buffer inside wasm memory and return its pointer."""
        if self._malloc is None:
            raise RuntimeError("malloc not exported from wasm module")
        size = length * itemsize
        ptr = self._malloc(self.store, size)
        if not isinstance(ptr, int):
            # On some versions of wasmtime, the return value may be a Val
            ptr = int(ptr)
        return ptr

    def _free_buf(self, ptr: int) -> None:
        if self._free is not None and ptr:
            self._free(self.store, ptr)

    def update(
        self,
        zoneTemp: Sequence[float],
        zoneCoolingSpt: Sequence[float],
        zoneDemand: Sequence[float],
        vavFlow: Sequence[float],
        vavFlowSpt: Sequence[float],
        vavDamperCmd: Sequence[float],
        dt_sec: float,
        is_imperial: bool,
    ) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
        """
        Compute requests for all zones. Input sequences must have length
        equal to the number of zones specified at construction time.

        Parameters:
            zoneTemp        – zone temperatures
            zoneCoolingSpt  – cooling setpoints
            zoneDemand      – demand percentages (0–100)
            vavFlow         – measured airflow values
            vavFlowSpt      – airflow setpoints
            vavDamperCmd    – damper positions (0–100)
            dt_sec          – time since last update in seconds
            is_imperial     – True for °F thresholds, False for °C

        Returns:
            coolRequests, pressureRequests
            Each is a tuple of ints (0–3) per zone.
        """
        n = self.n_zones
        assert len(zoneTemp) == n
        assert len(zoneCoolingSpt) == n
        assert len(zoneDemand) == n
        assert len(vavFlow) == n
        assert len(vavFlowSpt) == n
        assert len(vavDamperCmd) == n

        # Utility to copy a Python sequence into wasm memory
        def copy_in(array: Sequence[float]) -> int:
            ptr = self._alloc(n, ctypes.sizeof(ctypes.c_double))
            # memoryview for raw memory
            buf = self.mem.buffer
            # Create a ctypes array view into the buffer
            c_array = (ctypes.c_double * n).from_buffer(buf, ptr)
            for i in range(n):
                c_array[i] = float(array[i])
            return ptr

        # Utility to copy an int array out of wasm memory
        def copy_out_int(ptr: int) -> Tuple[int, ...]:
            buf = self.mem.buffer
            c_array = (ctypes.c_int * n).from_buffer(buf, ptr)
            return tuple(int(c_array[i]) for i in range(n))

        # Allocate and copy all input arrays
        p_zoneTemp = copy_in(zoneTemp)
        p_zoneCoolingSpt = copy_in(zoneCoolingSpt)
        p_zoneDemand = copy_in(zoneDemand)
        p_vavFlow = copy_in(vavFlow)
        p_vavFlowSpt = copy_in(vavFlowSpt)
        p_vavDamperCmd = copy_in(vavDamperCmd)
        # Allocate output arrays
        p_coolReq = self._alloc(n, ctypes.sizeof(ctypes.c_int))
        p_pressReq = self._alloc(n, ctypes.sizeof(ctypes.c_int))

        # Call update: signature (pTemp, pSp, pDemand, pFlow, pFlowSp, pDamper, dt, isImperial, nZones, pCool, pPress)
        # Emscripten exports int32 parameters, floats as doubles; booleans as ints
        self._update(
            self.store,
            p_zoneTemp,
            p_zoneCoolingSpt,
            p_zoneDemand,
            p_vavFlow,
            p_vavFlowSpt,
            p_vavDamperCmd,
            ctypes.c_double(dt_sec),
            1 if is_imperial else 0,
            n,
            p_coolReq,
            p_pressReq,
        )

        # Copy outputs back to Python
        cool = copy_out_int(p_coolReq)
        press = copy_out_int(p_pressReq)

        # Free allocated memory
        for ptr in (p_zoneTemp, p_zoneCoolingSpt, p_zoneDemand, p_vavFlow, p_vavFlowSpt, p_vavDamperCmd, p_coolReq, p_pressReq):
            self._free_buf(ptr)

        return cool, press


__all__ = ["HVACAlgo"]