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
    from wasmtime import (
        Store,
        Module,
        Linker,
        WasiConfig,
        Memory,
        Func,
        FuncType,
        ValType,
        Global,
        GlobalType,
        Table,
        TableType,
        Limits,
        MemoryType,
    )
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
        # Instantiate via a Linker so WASI (if present) can be wired automatically.
        # With the updated build.sh we also export memory (no env.memory import).
        self.linker = Linker(self.store.engine)
        try:
            self.store.set_wasi(WasiConfig())
            self.linker.define_wasi()
        except Exception:
            # If the module has no WASI imports, this is fine.
            pass
        # If the module still has Emscripten-style imports (common across emcc
        # versions), automatically provide minimal stubs so instantiation works.
        self._auto_define_imports()

        self.instance = self.linker.instantiate(self.store, self.module)
        # Exports
        exports = self.instance.exports(self.store)
        # Linear memory may be exported as "memory". Some emcc builds import
        # env.memory instead; in that case, _auto_define_imports provides it.
        self.mem: Memory = exports.get("memory")
        if self.mem is None:
            # Try to resolve memory from the linker-defined import.
            try:
                self.mem = self._imported_memory
            except AttributeError:
                raise KeyError("No exported or imported WebAssembly memory named 'memory'")

        def pick(*names):
            for n in names:
                if n in exports:
                    return exports[n]
            raise KeyError(f"Missing expected export. Tried: {names}. Available: {list(exports.keys())}")

        # Emscripten may export with or without leading underscore depending on flags.
        self._init = pick("hvac_init", "_hvac_init")
        self._update = pick("hvac_update", "_hvac_update")
        self._malloc = exports.get("malloc") or exports.get("_malloc")
        self._free = exports.get("free") or exports.get("_free")
        # Remember zone count
        self.n_zones = n_zones
        # Call init once to allocate internal state inside the module
        self._init(self.store, n_zones)

    def _auto_define_imports(self) -> None:
        """Provide minimal imports required by some emcc-produced wasm modules.

        This keeps the host resilient across Emscripten versions. It inspects
        the module import section and defines any missing `env.*` imports.

        For functions, we define no-op stubs that return 0.
        For globals/tables/memory, we create the appropriate objects.
        """

        # Track a memory object if the module expects env.memory
        self._imported_memory = None

        for imp in self.module.imports:
            mod = getattr(imp, "module", None)
            name = getattr(imp, "name", None)
            ty = getattr(imp, "type", None)
            if mod is None or name is None or ty is None:
                continue

            # Wasmtime's Linker doesn't provide a simple "is defined" API.
            # We'll just attempt to define, and ignore "already defined" errors.
            try:
                if isinstance(ty, MemoryType):
                    mem = Memory(self.store, ty)
                    self.linker.define(self.store, mod, name, mem)
                    if name == "memory":
                        self._imported_memory = mem

                elif isinstance(ty, TableType):
                    table = Table(self.store, ty, None)
                    self.linker.define(self.store, mod, name, table)

                elif isinstance(ty, GlobalType):
                    # Default initialise globals to 0.
                    init = 0
                    g = Global(self.store, ty, init)
                    self.linker.define(self.store, mod, name, g)

                elif isinstance(ty, FuncType):
                    # No-op stub: returns 0 (or None) depending on signature.
                    def _stub(*args):
                        if len(ty.results) == 0:
                            return None
                        # Return 0 of correct type
                        r = ty.results[0]
                        if r == ValType.f32() or r == ValType.f64():
                            return 0.0
                        return 0

                    func = Func(self.store, ty, _stub)
                    self.linker.define(self.store, mod, name, func)

            except Exception:
                # Ignore duplicates or definitions that fail because this import is
                # already wired by WASI or previous definitions.
                continue

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




    def _base_ptr(self) -> int:
        p = self.mem.data_ptr(self.store)
        # p can be a ctypes pointer, bytes-like, or buffer-ish depending on wasmtime build
        if isinstance(p, int):
            return p
        if isinstance(p, (bytes, bytearray)):
            # should not happen for data_ptr, but guard anyway
            raise RuntimeError("wasmtime Memory.data_ptr returned bytes; cannot get base pointer")
        # ctypes pointer path:
        try:
            return ctypes.cast(p, ctypes.c_void_p).value
        except Exception:
            # fallback: address-of first element
            return ctypes.addressof(p.contents)


    def _mem_len(self) -> int:
        return int(self.mem.data_len(self.store))

    def _ptr_to_address(self, ptr: int, size: int) -> int:
        """Convert a wasm offset pointer to a host address, with bounds check."""
        if ptr < 0 or size < 0 or (ptr + size) > self._mem_len():
            raise RuntimeError(f"wasm memory out of bounds: ptr={ptr} size={size} mem_len={self._mem_len()}")
        return self._base_ptr() + int(ptr)
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
            addr = self._ptr_to_address(ptr, n * ctypes.sizeof(ctypes.c_double))
            c_array = (ctypes.c_double * n).from_address(addr)
            for i in range(n):
                c_array[i] = float(array[i])
            return ptr

        # Utility to copy an int array out of wasm memory
        def copy_out_int(ptr: int) -> Tuple[int, ...]:
            addr = self._ptr_to_address(ptr, n * ctypes.sizeof(ctypes.c_int))
            c_array = (ctypes.c_int * n).from_address(addr)
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
            float(dt_sec), 
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