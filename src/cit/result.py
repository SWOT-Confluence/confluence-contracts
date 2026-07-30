"""Read model for an actual produced file (the ACTUAL side).

Where :mod:`cit.models` describes what a contract *expects*, this module exposes what a
file *actually* contains, in a uniform shape the validators can compare against. The class
hierarchy is organized by structural family, not by module, so most modules need no new
code.

Planned (P1-4):

- ``Result`` (ABC) -- the read-model surface: ``filepath``, ``dimensions``,
  ``global_attributes``, ``variables`` (name -> dtype/shape/dims), ``variable_attributes``,
  and the fill-values found in the file; abstract ``_read()`` hook.
- ``ReachResult`` -- flat per-reach NetCDF (momma + ~14 others); the default reader.
- ``JsonResult`` -- JSON products (setfinder/combine_data/coordination files), exposing the
  same surface so the structural validator treats them identically.
- ``GroupedResult`` / ``AlgoIndexedResult`` -- stubs for later; escape hatches for nested
  (validation, moi) and algorithm-indexed (nse/kge) layouts.

Backed by :mod:`cit.netcdf` for the actual file walking.
"""

import functools
from abc import ABC
from dataclasses import dataclass

import numpy as np

from netcdf import Netcdf, numpy_to_token


@dataclass(frozen=True)
class VarInfo:
    dtype: str                      # contract token: "f8", "i4", "S1", ...
    dims: tuple[str, ...]
    shape: tuple[int, ...]


class Result(ABC):
    """"""

    def __init__(self, filepath):
        self._filepath = filepath

    @property
    def filepath(self) -> str:
        return self._filepath

    def close(self):
        ...

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class NetcdfResult(Result):
    """"""

    def __init__(self):
        self._nc = Netcdf(self._filepath)

    def close(self):
        self._nc.close()

    @functools.cached_property
    def global_attributes(self) -> dict:
        return self._nc.global_attributes()

    @functools.cached_property
    def dimensions(self) -> dict:
        return self._nc.dimensions()

    @functools.cached_property
    def variables(self) -> dict:
        return {
            name: VarInfo(numpy_to_token(dtype), dims, shape)
            for name, dtype, dims, shape in self._nc.iter_variables()
        }

    @functools.cached_property
    def variable_attributes(self) -> dict:
        return self._nc.variable_attributes()

    @functools.cached_property
    def unit_fill_values(self) -> dict[str, set]:
        """"""
        fills: dict[str, set] = {}
        for name, info in self.variables.items():
            attrs = self.variable_attributes.get(name, {})
            if "_FillValue" not in attrs:
                continue
            fills.setdefault(info.dtype, set()).add(attrs["_FillValue"])
        return fills
