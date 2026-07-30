"""Read model for an actual produced file (the ACTUAL side).

Where :mod:`cit.models` describes what a contract *expects*, this module exposes what a file
*actually* contains, in a uniform shape the validators and the contract parser compare against.
The hierarchy is organized by file *format*. Only netCDF result files are read, via
:class:`NetcdfResult`. The workflow's JSON files (reach/set/basin/continent manifests) are
execution *inputs* that drive orchestration, not module *results*, so no JSON reader is included;
one would be added only if a module ever writes an intermediate *result* as JSON.

Implements (P1-4):

- :class:`Result` -- the abstract base. It carries only the lazy lifecycle common to every
  format: cheap construction (reads nothing), ``filepath``, an abstract ``close()``, and
  context-manager support. Format-specific read-model fields live on the subclasses.
- :class:`NetcdfResult` -- the one concrete netCDF reader; flat, grouped, and algorithm-indexed
  files all reduce to the same read model. Exposes ``dimensions``, ``variables``,
  ``variable_attributes``, and ``global_attributes`` as lazily-read, cached properties, backed by
  :class:`cit.netcdf.Netcdf`.
- :class:`VarInfo` -- one netCDF variable's structure (dtype token, dims, shape).

Every field that reads the file is a :func:`functools.cached_property`: nothing is read until a
property is accessed, each is read once and cached, and ``close()`` releases the file handle while
the cached metadata stays available. Only metadata is read -- never data arrays -- so a result's
footprint stays small regardless of file size.

Backed by :mod:`cit.netcdf` for the actual file walking.
"""

import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass

from cit.netcdf import Netcdf, numpy_to_token


@dataclass(frozen=True)
class VarInfo:
    """One netCDF variable's structure, as read from the file.

    Attributes:
        dtype: Contract dtype token from :func:`cit.netcdf.numpy_to_token` (e.g. ``"f8"``,
            ``"i4"``, ``"S1"``, ``"str"``). A plain ``str`` rather than the ``DataType`` Literal,
            because a file may hold a dtype outside the contract vocabulary.
        dims: The variable's dimension names, in order.
        shape: The variable's dimension sizes, in order (parallel to ``dims``).
    """

    dtype: str
    dims: tuple[str, ...]
    shape: tuple[int, ...]


class Result(ABC):
    """Abstract base for a read model of one produced file.

    Subclasses expose format-specific fields; the base provides only the lazy lifecycle shared by
    every format. Construction reads nothing, so a caller can hold many results cheaply and read
    (and :meth:`close`) each one as it is processed.
    """

    def __init__(self, filepath: str) -> None:
        """Store the file path; no file access happens here.

        Args:
            filepath: Path to the produced file this result reads.
        """
        self._filepath = filepath

    @property
    def filepath(self) -> str:
        """The path of the file this result reads."""
        return self._filepath

    @abstractmethod
    def close(self) -> None:
        """Release any open file handle held by this result."""

    def __enter__(self) -> "Result":
        """Enter a context that guarantees a :meth:`close` on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the result when leaving the context."""
        self.close()


class NetcdfResult(Result):
    """Read model for any netCDF product, backed by :class:`cit.netcdf.Netcdf`.

    A single reader handles flat, grouped, and algorithm-indexed files: nested variables arrive
    group-qualified (``group/var``) and dimensions are read from the file, so no per-layout
    subclass is needed. Each read-model field is a lazily-read, cached property; only metadata is
    read, never data arrays.
    """

    def __init__(self, filepath: str) -> None:
        """Store the path and prepare a lazy netCDF handle (opened on first read).

        Args:
            filepath: Path to the ``.nc`` file this result reads.
        """
        super().__init__(filepath)
        self._nc = Netcdf(filepath)

    def close(self) -> None:
        """Close the underlying netCDF handle; cached metadata remains available."""
        self._nc.close()

    @functools.cached_property
    def dimensions(self) -> dict[str, int]:
        """Dimension sizes as ``{name: size}``, read once on first access."""
        return self._nc.dimensions()

    @functools.cached_property
    def variables(self) -> dict[str, VarInfo]:
        """Every variable's structure, keyed by qualified name (``group/var`` when nested)."""
        return {
            name: VarInfo(numpy_to_token(dtype), dims, shape)
            for name, dtype, dims, shape in self._nc.iter_variables()
        }

    @functools.cached_property
    def variable_attributes(self) -> dict[str, dict]:
        """All attributes for every variable (including ``_FillValue``), keyed by qualified name."""
        return self._nc.variable_attributes()

    @functools.cached_property
    def global_attributes(self) -> dict:
        """The file's root-level (global) attributes."""
        return self._nc.global_attributes()
