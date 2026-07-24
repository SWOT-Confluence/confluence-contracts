"""Low-level NetCDF reading: the single place that understands the file format.

The one layer with no local precedent in the Confluence codebase. Everything that reads a
``.nc`` file goes through here so the format details live in exactly one module, shared by
both the reader (:mod:`cit.result`) and the contract parser (:mod:`cit.parser`).

Planned (P1-3):

- ``open_nc`` -- open a NetCDF dataset.
- ``iter_variables(ds) -> (qualified_name, numpy_dtype, dim_names, shape)`` -- walk every
  variable, descending into nested groups and qualifying names as ``group/var``. Returns
  STRUCTURE only (name, dtype, dims, shape); it reads no attributes.
- ``variable_attributes(var) -> dict`` -- read all attributes on one variable (including
  ``_FillValue``), returned as a plain dict.
- ``global_attributes(ds) -> dict`` -- read the dataset-level (global) attributes.
- the single ``dtype token <-> numpy dtype`` map (e.g. ``f8``/``f4``/``i4``/``S1``), the
  bridge between a contract's ``dtype`` string and what NetCDF reports.

The attribute helpers live here, not in :mod:`cit.result`, because this module is the single
layer that touches the netCDF4 format; :mod:`cit.result` must not read netCDF4 directly and
instead assembles its ``variable_attributes`` / ``global_attributes`` / ``unit_fill_values``
from these helpers. They are kept separate from ``iter_variables`` so a structural-only run
does not pay to read metadata. Attribute consumers are ``RulesValidation`` (:mod:`cit.rules`)
and ``check_non_fill`` (:mod:`cit.validation`).
"""

from typing import get_args

import netCDF4 as nc
import numpy as np

from cit.models import DataType


class NetCDF:
    """Class to handle NetCDF I/O operations and data retrieval."""

    # token -> numpy dtype (contract dtype string → what NetCDF/numpy uses)
    TOKEN_TO_NUMPY = {token: np.dtype(token) for token in get_args(DataType)}

    def __init__(self, netcdf_file):
        self._path = netcdf_file
        self._fp = None

    @property
    def fp(self) -> nc.Dataset:
        """The open Dataset, opened on the first access and reopened if closed."""
        if self._fp is None or not self._fp.isopen():
            self._fp = self._open(self._path)
        return self._fp

    def _open(self, path) -> nc.Dataset:
        """Open the NetCDF dataset as read-only."""
        return nc.Dataset(path, "r")

    def close(self) -> None:
        """Close the handle if open; safe to call repeatedly."""
        if self._fp is not None and self._fp.isopen():
            self._fp.close()
        self._fp = None

    def __enter__(self):
        """Enter a context that guarantees a close() on exit."""
        return self

    def __exit__(self, *exc):
        """Close the handle when leaving the context."""
        self.close()

    def iter_variables(self):
        """Yield (qualified_name, numpy_dtype, dim_names, shape) for every variable.

        Descends into nested groups, qualifying names as ``group/var``.

        Yields:
            Tuples of (qualified_name, numpy_dtype, dim_names, shape).
        """
        yield from self._walk(self.fp, prefix="")

    def _walk(self, group, prefix):
        """Recursively yield variable structure from a group and its subgroups."""
        for name, var in group.variables.items():
            yield f"{prefix}{name}", var.dtype, tuple(var.dimensions), tuple(var.shape)

        for group_name, subgroup in group.groups.items():
            yield from self._walk(subgroup, prefix=f"{prefix}{group_name}/")


def numpy_to_token(dt: np.dtype) -> str:
    """Map a NetCDF-reported numpy dtype to a contract dtype token."""
    if dt is str:                      # NC_STRING (vlen) reports the Python str type
        return "str"                   # (or raise "unsupported" — not in DataType)
    return f"{dt.kind}{dt.itemsize}"   # byte-order-agnostic: '>f8', '<f8', '=f8' all -> 'f8'
