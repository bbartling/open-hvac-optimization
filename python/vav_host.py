"""
vav_host.py
---------------

Python wrapper for the Guideline‑36 VAV zone request algorithm compiled
to WebAssembly.  This module uses the wasmtime runtime to load the
`vav_algo.wasm` module produced by `c/build.sh` and exposes a
high‑level `VAVAlgo` class.  The wrapper hides the details of
allocating WebAssembly memory, copying input arrays and reading
results.  See tests/test_vav.py for example usage.

Note: Wasmtime’s Python API evolves over time.  This wrapper attempts
to be resilient by inspecting the module’s imports and satisfying
them with minimal stubs.  If you encounter `expected N imports`
errors when instantiating the module, ensure that the WASM file was
compiled with `-sSTANDALONE_WASM=1 -sALLOW_MEMORY_GROWTH=1` and
exported functions list matches the exported names below.
"""

import ctypes
import wasmtime


class VAVAlgo:
    """High‑level interface to the VAV request counter WebAssembly module."""

    def __init__(self, wasm_path: str, n_zones: int):
        """
        Create a new VAVAlgo instance.

        Parameters
        ----------
        wasm_path : str
            Path to the compiled `vav_algo.wasm` file.
        n_zones : int
            Number of VAV zones that will be processed.  This value
            determines the size of internal state allocated within the
            WebAssembly module.  It must match the length of the input
            arrays passed to :meth:`update`.
        """
        self.n_zones = int(n_zones)
        self.store = wasmtime.Store()
        self.module = wasmtime.Module.from_file(self.store.engine, wasm_path)

        # Set up a linker and satisfy any imports.  Many Emscripten
        # modules import `env.memory` or related globals/tables.  We
        # provide stub implementations here.  See
        # https://bytecodealliance.github.io/wasmtime/python/api.html for
        # details.
        linker = wasmtime.Linker(self.store.engine)

        # Predefine some stub functions for unexpected imports
        def _stub_func(*args):
            return None

        # Keep references to memory and table so they aren't garbage collected
        self._mem = None
        self._table = None

        for imp in self.module.imports:
            if imp.module == "env":
                if isinstance(imp.type, wasmtime.MemoryType):
                    # Provide a memory large enough for typical payloads.  10 pages
                    # (64 KiB/page) should be sufficient for modest array sizes.
                    self._mem = wasmtime.Memory(self.store, imp.type)
                    linker.define("env", imp.name, self._mem)
                elif isinstance(imp.type, wasmtime.TableType):
                    # Create a table with the given type.  The initial
                    # element is None.  Growable tables require a maximum.
                    self._table = wasmtime.Table(self.store, imp.type, None)
                    linker.define("env", imp.name, self._table)
                elif isinstance(imp.type, wasmtime.GlobalType):
                    # Define an immutable global initialised to zero.
                    g = wasmtime.Global(self.store, imp.type, 0)
                    linker.define("env", imp.name, g)
                elif isinstance(imp.type, wasmtime.FuncType):
                    # Provide a no‑op function matching the signature.
                    fn = wasmtime.Func(self.store, imp.type, _stub_func)
                    linker.define("env", imp.name, fn)
                else:
                    # Unhandled import type
                    raise RuntimeError(f"Unhandled import: {imp.module}.{imp.name} {imp.type}")

        # Instantiate the module
        self.instance = linker.instantiate(self.store, self.module)

        # Acquire exported memory (either imported above or exported by the module)
        exports = self.instance.exports(self.store)
        mem = exports.get("memory")
        if mem is None:
            # fall back to imported memory
            mem = self._mem
        self.memory = mem
        if self.memory is None:
            raise RuntimeError("WebAssembly module does not export or import a memory")

        # Resolve malloc/free and algorithm functions.  Emscripten adds
        # underscores to C function names when targeting standalone
        # WebAssembly.  Try without and with underscore.
        def _resolve(name):
            fn = exports.get(name)
            if fn is None:
                fn = exports.get(f"_{name}")
            if fn is None:
                raise RuntimeError(f"Function {name} not found in WASM module")
            return fn

        self._malloc = _resolve("malloc")
        self._free   = _resolve("free")
        self._init   = _resolve("vav_init")
        self._update = _resolve("vav_update")

        # Call init to allocate per‑zone state
        self._init(self.store, self.n_zones)

    # Helper to get the base pointer into wasm memory as an integer
    def _base_ptr(self) -> int:
        ptr_obj = self.memory.data_ptr(self.store)
        # Wasmtime >=0.39 returns a ctypes pointer.  Cast to c_void_p to get address.
        try:
            return ctypes.cast(ptr_obj, ctypes.c_void_p).value
        except Exception:
            # Fallback: assume int
            return int(ptr_obj)

    def _alloc_and_copy(self, data, ctype):
        """Allocate space in WASM memory and copy a Python sequence into it.

        Parameters
        ----------
        data : Sequence
            The Python sequence to copy (must support len() and index access).
        ctype : ctypes type
            The target ctypes scalar type (e.g. ctypes.c_double).
        Returns
        -------
        int
            The offset returned by malloc (to be passed into the WASM function).
        """
        count = len(data)
        size = ctypes.sizeof(ctype) * count
        # Allocate via wasm malloc
        ptr = self._malloc(self.store, size)
        # Copy into WASM memory
        addr = self._base_ptr() + int(ptr)
        array_type = (ctype * count)
        buffer = array_type.from_address(addr)
        for i in range(count):
            buffer[i] = data[i]
        return ptr

    def _read_int_array(self, ptr, count):
        """Read an array of 32‑bit ints from WASM memory."""
        addr = self._base_ptr() + int(ptr)
        array_type = (ctypes.c_int * count)
        buffer = array_type.from_address(addr)
        return [int(buffer[i]) for i in range(count)]

    def _free_ptr(self, ptr):
        if ptr:
            self._free(self.store, ptr)

    def update(self, zoneTemp, zoneCoolingSpt, zoneDemand,
               vavFlow, vavFlowSpt, vavDamperCmd, dt_sec, is_imperial=0):
        """
        Compute cooling and pressure requests for each zone.

        Parameters
        ----------
        zoneTemp, zoneCoolingSpt, zoneDemand, vavFlow, vavFlowSpt, vavDamperCmd : list[float]
            Input arrays of length ``n_zones`` containing the current
            zone temperature, cooling setpoint, demand, flow, flow
            setpoint and damper command.
        dt_sec : float
            Elapsed time in seconds since the previous call.  The
            timers inside the algorithm are advanced by this amount.
        is_imperial : int, optional
            Set to 1 to use Fahrenheit thresholds, 0 to use Celsius.

        Returns
        -------
        tuple[list[int], list[int]]
            A tuple of two lists: (coolRequests, pressureRequests), each
            of length ``n_zones`` containing integers 0–3.
        """
        n = self.n_zones
        # Allocate and copy inputs
        p_zoneTemp      = self._alloc_and_copy(zoneTemp, ctypes.c_double)
        p_zoneSp        = self._alloc_and_copy(zoneCoolingSpt, ctypes.c_double)
        p_zoneDemand    = self._alloc_and_copy(zoneDemand, ctypes.c_double)
        p_flow          = self._alloc_and_copy(vavFlow, ctypes.c_double)
        p_flowSp        = self._alloc_and_copy(vavFlowSpt, ctypes.c_double)
        p_damper        = self._alloc_and_copy(vavDamperCmd, ctypes.c_double)
        # Allocate outputs (ints)
        sz_int = ctypes.sizeof(ctypes.c_int) * n
        p_cool  = self._malloc(self.store, sz_int)
        p_press = self._malloc(self.store, sz_int)
        try:
            # Invoke the WASM function
            self._update(self.store,
                         p_zoneTemp, p_zoneSp, p_zoneDemand,
                         p_flow, p_flowSp, p_damper,
                         float(dt_sec), int(is_imperial), int(n),
                         p_cool, p_press)
            # Read back results
            cool  = self._read_int_array(p_cool, n)
            press = self._read_int_array(p_press, n)
        finally:
            # Free all allocated memory
            for ptr in (p_zoneTemp, p_zoneSp, p_zoneDemand, p_flow, p_flowSp, p_damper, p_cool, p_press):
                self._free_ptr(ptr)
        return cool, press
