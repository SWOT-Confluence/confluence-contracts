"""Tests for the ACTUAL-file read model (cit.result).

These exercise :class:`cit.result.NetcdfResult` (and its abstract base :class:`Result`) against a
synthetic momma-like file built with ``netCDF4`` in pytest's ``tmp_path``. Coverage:

- surface: dimensions, variables (VarInfo dtype/dims/shape), variable_attributes (incl. _FillValue),
  and global attributes match the file (the P1-4 acceptance criteria);
- vlen strings: a ``time_str`` NC_STRING variable comes back as the ``"str"`` dtype token, not a
  crash (the reason DataType grew a ``str`` token);
- laziness/lifecycle: construction reads nothing, first access reads, close() releases the handle,
  the context manager closes on exit, and cached metadata survives close();
- the base class is abstract and cannot be instantiated directly.
"""

import netCDF4 as nc
import numpy as np
import pytest

from cit.result import NetcdfResult, Result, VarInfo


@pytest.fixture
def momma_nc(tmp_path):
    """Write a momma-like flat NetCDF file and return its path.

    Root dimension ``nt`` (size 4); a dimensioned ``stage`` (f8, with ``_FillValue``); a scalar
    ``reach_id`` (i8); a vlen ``time_str`` (NC_STRING); and the momma global attributes ``valid``
    and ``reach_id``. The write handle is closed before returning.

    Args:
        tmp_path: pytest-provided temporary directory unique to the test.

    Returns:
        Path to the written ``.nc`` file.
    """
    path = tmp_path / "12590000211_momma.nc"
    ds = nc.Dataset(path, "w")

    ds.createDimension("nt", 4)
    ds.setncattr("valid", np.int32(1))
    ds.setncattr("reach_id", np.int64(12590000211))

    stage = ds.createVariable("stage", "f8", ("nt",), fill_value=-999999999999.0)
    stage.setncattr("long_name", "water surface elevation")
    stage.setncattr("units", "m")

    ds.createVariable("reach_id", "i8", ())
    ds.createVariable("time_str", str, ("nt",))  # vlen NC_STRING

    ds.close()
    return path


def test_dimensions(momma_nc):
    """The dimensions property reports the root dimension sizes."""
    with NetcdfResult(momma_nc) as result:
        assert result.dimensions == {"nt": 4}


def test_variables_structure_and_vlen_dtype(momma_nc):
    """Each variable's VarInfo is exposed; the vlen time_str tokenizes to 'str'."""
    with NetcdfResult(momma_nc) as result:
        variables = result.variables

    assert set(variables) == {"stage", "reach_id", "time_str"}
    assert variables["stage"] == VarInfo(dtype="f8", dims=("nt",), shape=(4,))
    assert variables["reach_id"] == VarInfo(dtype="i8", dims=(), shape=())
    assert variables["time_str"].dtype == "str"  # vlen NC_STRING, not a crash


def test_variable_attributes_include_fill_value(momma_nc):
    """variable_attributes carries a variable's attributes, including _FillValue."""
    with NetcdfResult(momma_nc) as result:
        stage_attrs = result.variable_attributes["stage"]

    assert stage_attrs["units"] == "m"
    assert "_FillValue" in stage_attrs
    assert float(stage_attrs["_FillValue"]) == -999999999999.0


def test_global_attributes(momma_nc):
    """global_attributes returns the file's root-level attributes."""
    with NetcdfResult(momma_nc) as result:
        globals_ = result.global_attributes

    assert set(globals_) == {"valid", "reach_id"}
    assert int(globals_["reach_id"]) == 12590000211


def test_construction_reads_nothing_then_lazily_reads(momma_nc):
    """Constructing a result opens no handle; touching a property triggers the read."""
    result = NetcdfResult(momma_nc)
    assert result._nc._fp is None  # nothing opened yet

    _ = result.variables  # first access opens + reads
    assert result._nc._fp is not None

    result.close()


def test_close_releases_handle_but_keeps_cache(momma_nc):
    """close() releases the file handle; already-cached metadata stays available."""
    result = NetcdfResult(momma_nc)
    variables = result.variables  # populate the cache

    result.close()
    assert result._nc._fp is None  # handle released
    assert result.variables is variables  # cached value still served, file closed


def test_context_manager_closes_on_exit(momma_nc):
    """The context manager closes the underlying handle on block exit."""
    with NetcdfResult(momma_nc) as result:
        _ = result.variables
        assert result._nc._fp is not None

    assert result._nc._fp is None


def test_filepath_property(momma_nc):
    """The filepath property returns the path the result was constructed with."""
    result = NetcdfResult(str(momma_nc))
    assert result.filepath == str(momma_nc)


def test_result_base_is_abstract():
    """Result is abstract (abstract close) and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Result("some_path.nc")
