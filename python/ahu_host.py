"""
ahu_host.py
---------------

Python wrapper for the Guideline‑36 AHU Trim & Respond algorithms
compiled to WebAssembly.  This module uses the wasmtime runtime to
load the `ahu_algo.wasm` module produced by `c/build.sh` and
exposes a high‑level `AHUAlgo` class.  The wrapper satisfies the
WebAssembly module's imports (memory, table, globals) and resolves
the exported functions.  Two independent control loops are exposed:
``pressure`` for duct static pressure reset and ``sat`` for supply
air temperature reset.  Each must be initialised via the
corresponding ``init_*`` method before calling ``update_*``.

Usage example::

    from ahu_host import AHUAlgo

    algo = AHUAlgo("c/ahu/ahu_algo.wasm")
    # configure pressure loop
    algo.init_pressure(
        sp0=1.5, spmin=1.0, spmax=2.5,
        startup_delay_sec=10.0, update_interval_sec=30.0,
        ignore_req=0.5, sp_trim=-0.05, sp_respond=0.1, sp_respond_max=0.3
    )
    # configure SAT loop
    algo.init_sat(
        sp0=55.0, spmin=50.0, spmax=65.0,
        startup_delay_sec=10.0, update_interval_sec=30.0,
        ignore_req=1.0, sp_trim=0.5, sp_respond=-1.0, sp_respond_max=-2.0
    )
    # update both loops each timestep
    pressure_sp = algo.update_pressure(fanRun=1, current_sp=1.5,
                                      total_requests=5.0, dt_sec=30.0)
    sat_sp = algo.update_sat(fanRun=1, current_sp=55.0,
                             total_requests=3.0, outside_air_temp=70.0,
                             oat_min=50.0, oat_max=80.0, dt_sec=30.0)

See tests/test_ahu.py for more extensive examples.
"""

import ctypes
import wasmtime


class AHUAlgo:
    """High‑level interface to the AHU Trim & Respond WebAssembly module."""

    def __init__(self, wasm_path: str):
        """Load the WASM module and prepare for use.

        Parameters
        ----------
        wasm_path : str
            Path to the compiled `ahu_algo.wasm` file.  Use
            ``c/build.sh`` to build this binary.
        """
        self.store = wasmtime.Store()
        self.module = wasmtime.Module.from_file(self.store.engine, wasm_path)

        # Provide stub imports for Emscripten modules
        linker = wasmtime.Linker(self.store.engine)

        # Default stub function for imported functions
        def _stub_func(*_args):
            return None

        # Keep references to memory/table so they persist
        self._mem = None
        self._table = None

        # Satisfy imports
        for imp in self.module.imports:
            if imp.module == "env":
                if isinstance(imp.type, wasmtime.MemoryType):
                    # Allocate memory with same type
                    self._mem = wasmtime.Memory(self.store, imp.type)
                    linker.define("env", imp.name, self._mem)
                elif isinstance(imp.type, wasmtime.TableType):
                    self._table = wasmtime.Table(self.store, imp.type, None)
                    linker.define("env", imp.name, self._table)
                elif isinstance(imp.type, wasmtime.GlobalType):
                    g = wasmtime.Global(self.store, imp.type, 0)
                    linker.define("env", imp.name, g)
                elif isinstance(imp.type, wasmtime.FuncType):
                    fn = wasmtime.Func(self.store, imp.type, _stub_func)
                    linker.define("env", imp.name, fn)
                else:
                    raise RuntimeError(f"Unhandled import type: {imp.module}.{imp.name} {imp.type}")

        # Instantiate module
        self.instance = linker.instantiate(self.store, self.module)

        # Exported memory may exist; fallback to imported memory
        exports = self.instance.exports(self.store)
        mem = exports.get("memory")
        if mem is None:
            mem = self._mem
        self.memory = mem
        if self.memory is None:
            raise RuntimeError("WebAssembly module does not define or import memory")

        # Resolve required functions (with or without underscore)
        def _resolve(name):
            fn = exports.get(name)
            if fn is None:
                fn = exports.get(f"_{name}")
            if fn is None:
                raise RuntimeError(f"Function {name} not found in WASM module")
            return fn

        # Memory allocation
        self._malloc = _resolve("malloc")
        self._free   = _resolve("free")
        # Pressure control
        self._init_pressure  = _resolve("ahu_init_pressure")
        self._update_pressure = _resolve("ahu_update_pressure")
        # SAT control
        self._init_sat  = _resolve("ahu_init_sat")
        self._update_sat = _resolve("ahu_update_sat")

        # Track whether loops have been initialised
        self._pressure_initialised = False
        self._sat_initialised = False

    def init_pressure(self, sp0: float, spmin: float, spmax: float,
                      startup_delay_sec: float, update_interval_sec: float,
                      ignore_req: float, sp_trim: float,
                      sp_respond: float, sp_respond_max: float):
        """Initialise the duct static pressure reset loop.

        Parameters mirror the C API; see ahu_algo.h for details.
        """
        self._init_pressure(
            self.store,
            float(sp0), float(spmin), float(spmax),
            float(startup_delay_sec), float(update_interval_sec),
            float(ignore_req), float(sp_trim),
            float(sp_respond), float(sp_respond_max)
        )
        self._pressure_initialised = True

    def update_pressure(self, fanRun: int, current_sp: float,
                        total_requests: float, dt_sec: float) -> float:
        """Update the pressure setpoint and return the new value.

        Parameters
        ----------
        fanRun : int
            1 if the fan is running, 0 otherwise.
        current_sp : float
            The current setpoint in use.
        total_requests : float
            Sum of all zone pressure requests.
        dt_sec : float
            Elapsed time since the previous call.

        Returns
        -------
        float
            The updated duct static pressure setpoint.
        """
        if not self._pressure_initialised:
            raise RuntimeError("init_pressure() must be called before update_pressure()")
        # Wasmtime will convert Python ints/floats to the appropriate wasm types
        return float(self._update_pressure(
            self.store,
            int(fanRun), float(current_sp), float(total_requests), float(dt_sec)
        ))

    def init_sat(self, sp0: float, spmin: float, spmax: float,
                 startup_delay_sec: float, update_interval_sec: float,
                 ignore_req: float, sp_trim: float,
                 sp_respond: float, sp_respond_max: float):
        """Initialise the supply air temperature reset loop.

        Parameters mirror the C API; see ahu_algo.h for details.  The
        sp_trim parameter should typically be positive (warmer) while
        sp_respond should be negative (cooler) for cooling SAT reset.
        """
        self._init_sat(
            self.store,
            float(sp0), float(spmin), float(spmax),
            float(startup_delay_sec), float(update_interval_sec),
            float(ignore_req), float(sp_trim),
            float(sp_respond), float(sp_respond_max)
        )
        self._sat_initialised = True

    def update_sat(self, fanRun: int, current_sp: float,
                   total_requests: float, outside_air_temp: float,
                   oat_min: float, oat_max: float, dt_sec: float) -> float:
        """Update the SAT setpoint and return the new value.

        Parameters mirror the C API; see ahu_algo.h for details.  The
        outside air temperature parameters are currently ignored by the
        underlying algorithm but are accepted for API stability.
        """
        if not self._sat_initialised:
            raise RuntimeError("init_sat() must be called before update_sat()")
        return float(self._update_sat(
            self.store,
            int(fanRun), float(current_sp), float(total_requests),
            float(outside_air_temp), float(oat_min), float(oat_max), float(dt_sec)
        ))