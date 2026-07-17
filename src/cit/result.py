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
