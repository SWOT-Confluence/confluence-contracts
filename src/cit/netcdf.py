"""Low-level NetCDF reading: the single place that understands the file format.

The one layer with no local precedent in the Confluence codebase. Everything that reads a
``.nc`` file goes through here so the format details live in exactly one module, shared by
both the reader (:mod:`cit.result`) and the contract parser (:mod:`cit.parse`).

Attribute reads are split from structural iteration so a structural-only run does not pay to
read metadata; :mod:`cit.result` must not read netCDF4 directly and instead assembles its own
attribute dicts from the helpers here, keeping this module the sole layer that touches netCDF4.
"""

from collections.abc import Iterator
from typing import get_args

import netCDF4 as nc
import numpy as np

from cit.contract import DataType


class Netcdf:
    """Class to handle NetCDF I/O operations and data retrieval."""

    # token -> numpy dtype. netCDF4 reports a vlen NC_STRING as the Python `str` type, so "str"
    # is overridden below rather than left as np.dtype("str") -- that is <U0 and breaks the round-trip.
    TOKEN_TO_NUMPY = {token: np.dtype(token) for token in get_args(DataType)}
    TOKEN_TO_NUMPY["str"] = str

    def __init__(self, netcdf_file: str) -> None:
        """Store the file path; the dataset opens lazily on first access.

        Args:
            netcdf_file: Path to the ``.nc`` file to read.
        """
        self._path = netcdf_file
        self._fp = None

    @property
    def fp(self) -> nc.Dataset:
        """The open Dataset, opened on the first access and reopened if closed.

        Returns:
            The open ``netCDF4.Dataset`` handle for this file.
        """
        if self._fp is None or not self._fp.isopen():
            self._fp = self._open(self._path)
        return self._fp

    def _open(self, path: str) -> nc.Dataset:
        """Open the NetCDF dataset as read-only.

        Args:
            path: Path to the ``.nc`` file to open.

        Returns:
            The opened, read-only ``netCDF4.Dataset``.
        """
        return nc.Dataset(path, "r")

    def close(self) -> None:
        """Close the handle if open; safe to call repeatedly."""
        if self._fp is not None and self._fp.isopen():
            self._fp.close()
        self._fp = None

    def __enter__(self) -> "Netcdf":
        """Enter a context that guarantees a close() on exit.

        Returns:
            This instance.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the handle when leaving the context.

        Args:
            exc: The exception type, value, and traceback, if the block raised; unused, since
                the handle is closed unconditionally.
        """
        self.close()

    def _walk(self, group: nc.Dataset, prefix: str = "") -> Iterator[tuple[str, nc.Variable]]:
        """Yield (qualified_name, variable) for every variable, descending into groups.

        Args:
            group: The dataset or group to walk.
            prefix: The qualified-name prefix accumulated from enclosing groups (e.g.
                ``"group/"``); empty at the root.

        Yields:
            Tuples of the variable's qualified name (``group/var`` for a nested variable) and
            the variable object itself.
        """
        for name, var in group.variables.items():
            yield f"{prefix}{name}", var

        for group_name, subgroup in group.groups.items():
            yield from self._walk(subgroup, prefix=f"{prefix}{group_name}/")

    def iter_variables(self) -> Iterator[tuple[str, np.dtype, tuple, tuple]]:
        """Structure only: (qualified_name, numpy_dtype, dim_names, shape).

        Yields:
            Tuples of qualified name, numpy dtype, dimension names, and shape, for every
            variable in the file. Reads no attributes.
        """
        for name, var in self._walk(self.fp):
            yield name, var.dtype, tuple(var.dimensions), tuple(var.shape)

    @staticmethod
    def _attrs(obj: nc.Dataset | nc.Variable) -> dict:
        """Read all netCDF attributes off a Dataset or Variable (incl. _FillValue).

        Args:
            obj: The dataset or variable to read attributes from.

        Returns:
            A mapping of attribute name to value.
        """
        return {name: obj.getncattr(name) for name in obj.ncattrs()}

    def variable_attributes(self) -> dict[str, dict]:
        """All attributes for every variable, keyed by qualified name.

        Returns:
            A mapping of qualified variable name to its attribute dict.
        """
        return {name: self._attrs(var) for name, var in self._walk(self.fp)}

    def global_attributes(self) -> dict:
        """Dataset-level (global) attributes — root only, not per-group.

        Returns:
            A mapping of global attribute name to value.
        """
        return self._attrs(self.fp)

    def dimensions(self) -> dict[str, int]:
        """Root-level dimensions as ``{name: size}`` (e.g. momma → ``{"nt": 68}``).

        Root-only for now; nested/group dimensions are handled when
        :class:`cit.result.GroupedResult` lands.

        Returns:
            Mapping of each root dimension name to its size.
        """
        return {name: dim.size for name, dim in self.fp.dimensions.items()}


def numpy_to_token(dt: np.dtype) -> str:
    """Map a NetCDF-reported dtype to a contract dtype token.

    netCDF4 reports a vlen ``NC_STRING`` variable as the Python ``str`` type rather than an
    ``np.dtype``; that maps to the ``"str"`` token. Every real ``np.dtype`` maps by kind and
    itemsize, byte-order-agnostically (``>f8``/``<f8``/``=f8`` all → ``f8``).

    Args:
        dt: The dtype netCDF4 reports — an ``np.dtype``, or the ``str`` type for a vlen string.

    Returns:
        The contract dtype token (e.g. ``f8``, ``i4``, ``S1``, ``str``).
    """
    if dt is str:  # netCDF4 reports the Python str type for vlen NC_STRING
        return "str"
    return f"{dt.kind}{dt.itemsize}"  # byte-order-agnostic: '>f8', '<f8', '=f8' all -> 'f8'
