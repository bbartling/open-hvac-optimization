
"""
system_host.py
================

Wrapper for the combined Guideline-36 VAV + AHU WebAssembly module
built from ``c/system/system_algo.c``. The goal is to let a single
wasm instance supervise one AHU with N VAV boxes:

* VAV side: per-zone cooling & pressure requests
* AHU side: duct static pressure & SAT Trim & Respond with startup delay

The C side maintains:

* per-zone hysteresis & timers (same as ``hvac_algo.c``)
* AHU Trim & Respond internal state (last setpoints, elapsed startup, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import ctypes

from wasmtime import (
    Store,
    Module,
    Linker,
    WasiConfig,
    Memory,
    Func,
    FuncType,
    ValType,
    TableType,
    GlobalType,
    Global,
    Table,
    Limits,
    MemoryType,
)


@dataclass
class SystemConfig:
    n_zones: int
    # Pressure loop config
    p_sp0: float
    p_spmin: float
    p_spmax: float
    p_startup_delay_sec: float
    p_update_interval_sec: float
    p_ignore_req: float
    p_sp_trim: float
    p_sp_respond: float
    p_sp_respond_max: float
    # SAT loop config
    sat_sp0: float
    sat_spmin: float
    sat_spmax: float
    sat_startup_delay_sec: float
    sat_update_interval_sec: float
    sat_ignore_req: float
    sat_sp_trim: float
    sat_sp_respond: float
    sat_sp_respond_max: float


class SystemAlgo:
    """
    Thin wrapper around ``system/system_algo.wasm``.

    * ``dt_sec`` is wall-clock time since the previous call.
    * The C code internally integrates "time since fan has been running
      while occupied" using the ``fanRun`` and ``occupied`` booleans,
      exactly like the Java Guideline-36 reference.
    """

    def __init__(self, wasm_path: str, config: SystemConfig) -> None:
        self.config = config
        self.store = Store()
        self.module = Module.from_file(self.store.engine, wasm_path)
        self.linker = Linker(self.store.engine)

        # Minimal WASI wiring.
        wasi = WasiConfig()
        self.store.set_wasi(wasi)
        try:
            self.linker.define_wasi()
        except Exception:
            pass

        self._auto_define_imports()

        self.instance = self.linker.instantiate(self.store, self.module)
        exports = self.instance.exports(self.store)

        # memory
        self.mem: Memory = exports.get("memory") or exports.get("_memory")
        if self.mem is None:
            # If memory is only imported, provide our own.
            mem_ty = MemoryType(Limits(1, None), 0)
            self.mem = Memory(self.store, mem_ty)
            self.linker.define(self.store, "env", "memory", self.mem)

        def pick(*names: str):
            for n in names:
                if n in exports:
                    return exports[n]
            raise KeyError(f"Missing expected export; tried {names}, have {list(exports.keys())}")

        self._init = pick("system_init", "_system_init")
        self._update = pick("system_update", "_system_update")
        self._malloc = exports.get("malloc") or exports.get("_malloc")
        self._free = exports.get("free") or exports.get("_free")
        if self._malloc is None or self._free is None:
            raise RuntimeError("system_algo.wasm must export malloc/free")

        # Initialise C side
        cfg = self.config
        self._init(
            self.store,
            cfg.n_zones,
            cfg.p_sp0,
            cfg.p_spmin,
            cfg.p_spmax,
            cfg.p_startup_delay_sec,
            cfg.p_update_interval_sec,
            cfg.p_ignore_req,
            cfg.p_sp_trim,
            cfg.p_sp_respond,
            cfg.p_sp_respond_max,
            cfg.sat_sp0,
            cfg.sat_spmin,
            cfg.sat_spmax,
            cfg.sat_startup_delay_sec,
            cfg.sat_update_interval_sec,
            cfg.sat_ignore_req,
            cfg.sat_sp_trim,
            cfg.sat_sp_respond,
            cfg.sat_sp_respond_max,
        )

    # ------------------------------------------------------------------ helpers (borrowed from algo_host)

    def _auto_define_imports(self) -> None:
        for mod in ("env", "wasi_snapshot_preview1"):
            try:
                imports = self.module.imports
            except AttributeError:
                imports = self.module.imports(self.store.engine)
            for imp in imports:
                if imp.module != mod:
                    continue
                name = imp.name
                ty = imp.type
                try:
                    if isinstance(ty, MemoryType):
                        mem = Memory(self.store, ty)
                        self.linker.define(self.store, mod, name, mem)
                    elif isinstance(ty, TableType):
                        table = Table(self.store, ty, None)
                        self.linker.define(self.store, mod, name, table)
                    elif isinstance(ty, GlobalType):
                        g = Global(self.store, ty, 0)
                        self.linker.define(self.store, mod, name, g)
                    elif isinstance(ty, FuncType):
                        def _stub(*args):
                            if len(ty.results) == 0:
                                return None
                            r = ty.results[0]
                            if r == ValType.f32() or r == ValType.f64():
                                return 0.0
                            return 0
                        func = Func(self.store, ty, _stub)
                        self.linker.define(self.store, mod, name, func)
                except Exception:
                    continue

    def _alloc(self, length: int, itemsize: int) -> int:
        if self._malloc is None:
            raise RuntimeError("malloc not exported from wasm module")
        size = length * itemsize
        ptr = self._malloc(self.store, size)
        if not isinstance(ptr, int):
            ptr = int(ptr)
        return ptr

    def _free_buf(self, ptr: int) -> None:
        if self._free is not None and ptr:
            self._free(self.store, ptr)

    def _base_ptr(self) -> int:
        p = self.mem.data_ptr(self.store)
        if isinstance(p, (bytes, bytearray, memoryview)):
            raise RuntimeError("wasmtime Memory.data_ptr returned bytes; cannot get base pointer")
        try:
            return ctypes.cast(p, ctypes.c_void_p).value
        except Exception:
            return ctypes.addressof(p.contents)

    def _mem_len(self) -> int:
        return int(self.mem.data_len(self.store))

    def _ptr_to_address(self, ptr: int, size: int) -> int:
        if ptr < 0 or size < 0 or (ptr + size) > self._mem_len():
            raise RuntimeError(
                f"wasm memory out of bounds: ptr={ptr} size={size} mem_len={self._mem_len()}"
            )
        return self._base_ptr() + int(ptr)

    # ------------------------------------------------------------------ public API

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
        fanRun: int,
        occupied: int,
        current_pressure_sp: float,
        current_sat_sp: float,
        outside_air_temp: float,
        oat_min: float,
        oat_max: float,
    ) -> Tuple[Tuple[int, ...], Tuple[int, ...], float, float]:
        """
        Compute per-zone requests and next AHU setpoints.

        Returns:
            coolRequests, pressureRequests, next_pressure_sp, next_sat_sp
        """
        n = self.config.n_zones
        assert len(zoneTemp) == n
        assert len(zoneCoolingSpt) == n
        assert len(zoneDemand) == n
        assert len(vavFlow) == n
        assert len(vavFlowSpt) == n
        assert len(vavDamperCmd) == n

        def copy_in(array: Sequence[float]) -> int:
            ptr = self._alloc(n, ctypes.sizeof(ctypes.c_double))
            addr = self._ptr_to_address(ptr, n * ctypes.sizeof(ctypes.c_double))
            c_array = (ctypes.c_double * n).from_address(addr)
            for i, v in enumerate(array):
                c_array[i] = float(v)
            return ptr

        def copy_out_int(ptr: int) -> Tuple[int, ...]:
            addr = self._ptr_to_address(ptr, n * ctypes.sizeof(ctypes.c_int))
            c_array = (ctypes.c_int * n).from_address(addr)
            return tuple(int(c_array[i]) for i in range(n))

        # Allocate and copy inputs
        p_zoneTemp = copy_in(zoneTemp)
        p_zoneCoolingSpt = copy_in(zoneCoolingSpt)
        p_zoneDemand = copy_in(zoneDemand)
        p_vavFlow = copy_in(vavFlow)
        p_vavFlowSpt = copy_in(vavFlowSpt)
        p_vavDamperCmd = copy_in(vavDamperCmd)

        # Outputs
        p_coolReq = self._alloc(n, ctypes.sizeof(ctypes.c_int))
        p_pressReq = self._alloc(n, ctypes.sizeof(ctypes.c_int))
        p_next_p = self._alloc(1, ctypes.sizeof(ctypes.c_double))
        p_next_sat = self._alloc(1, ctypes.sizeof(ctypes.c_double))

        # Call wasm
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
            int(fanRun),
            int(occupied),
            float(current_pressure_sp),
            float(current_sat_sp),
            float(outside_air_temp),
            float(oat_min),
            float(oat_max),
            p_coolReq,
            p_pressReq,
            p_next_p,
            p_next_sat,
        )

        cool = copy_out_int(p_coolReq)
        press = copy_out_int(p_pressReq)

        # Read back setpoints
        addr_p = self._ptr_to_address(p_next_p, ctypes.sizeof(ctypes.c_double))
        addr_sat = self._ptr_to_address(p_next_sat, ctypes.sizeof(ctypes.c_double))
        next_p = float(ctypes.c_double.from_address(addr_p).value)
        next_sat = float(ctypes.c_double.from_address(addr_sat).value)

        # Free
        for ptr in (
            p_zoneTemp,
            p_zoneCoolingSpt,
            p_zoneDemand,
            p_vavFlow,
            p_vavFlowSpt,
            p_vavDamperCmd,
            p_coolReq,
            p_pressReq,
            p_next_p,
            p_next_sat,
        ):
            self._free_buf(ptr)

        return cool, press, next_p, next_sat


__all__ = ["SystemAlgo", "SystemConfig"]
