"""Tests for the low-level NetCDF reader (cit.netcdf).

These exercise :class:`cit.netcdf.NetCDF` and the module-level
:func:`cit.netcdf.numpy_to_token` against a synthetic file with a known structure,
built in code with the ``netCDF4`` library and written to pytest's ``tmp_path``.

The coverage spans four concerns:

- structure: :meth:`NetCDF.iter_variables` reports dtype/dims/shape for every variable,
  including scalars and slash-qualified names nested inside groups;
- attributes: :meth:`NetCDF.variable_attributes` keys by qualified name and includes
  ``_FillValue``, while :meth:`NetCDF.global_attributes` returns root-only attributes;
- lifecycle: lazy opening, idempotent close, reopen-on-access, context-manager close,
  and the missing-file error path;
- dtype mapping: the ``token <-> numpy dtype`` round-trip, map/vocabulary sync,
  byte-order agnosticism, and vlen-string rejection.
"""

from typing import get_args

import netCDF4 as nc
import numpy as np
import pytest

from cit.models import DataType
from cit.netcdf import NetCDF, numpy_to_token


@pytest.fixture
def sample_nc(tmp_path):
    """Write a NetCDF file with a known structure and return its path.

    The file contains a single root dimension ``nt`` (size 3), two global attributes,
    dimensioned and scalar root variables, and a ``gagecal`` subgroup with its own
    variable and a group-level attribute. The write handle is closed before returning
    so readers open the file cleanly.

    Args:
        tmp_path: pytest-provided temporary directory unique to the test.

    Returns:
        Path to the written ``.nc`` file.
    """
    path = tmp_path / "sample.nc"
    ds = nc.Dataset(path, "w")

    ds.createDimension("nt", 3)
    ds.setncattr("Conventions", "CF-1.7")
    ds.setncattr("title", "test file")

    stage = ds.createVariable("stage", "f8", ("nt",), fill_value=-9999.0)
    stage.setncattr("long_name", "water surface elevation")
    stage.setncattr("units", "m")

    zero_flow_stage = ds.createVariable("zero_flow_stage", "f8", ())
    zero_flow_stage.setncattr("units", "m")

    ds.createVariable("reach_id", "i8", ())

    grp = ds.createGroup("gagecal")
    grp.createVariable("q", "f4", ("nt",))
    grp.setncattr("group_note", "should not appear in globals")

    ds.close()
    return path


def test_iter_variables_reports_structure(sample_nc):
    """iter_variables yields dtype, dims, and shape for every variable by qualified name."""
    with NetCDF(sample_nc) as reader:
        collected = {
            name: (dtype, dims, shape) for name, dtype, dims, shape in reader.iter_variables()
        }

    assert set(collected) == {"stage", "zero_flow_stage", "reach_id", "gagecal/q"}

    dtype, dims, shape = collected["stage"]
    assert dtype == np.dtype("float64")
    assert dims == ("nt",)
    assert shape == (3,)

    dtype, dims, shape = collected["zero_flow_stage"]
    assert dtype == np.dtype("float64")
    assert dims == ()
    assert shape == ()

    dtype, dims, shape = collected["reach_id"]
    assert dtype == np.dtype("int64")
    assert dims == ()
    assert shape == ()

    dtype, dims, shape = collected["gagecal/q"]
    assert dtype == np.dtype("float32")
    assert dims == ("nt",)
    assert shape == (3,)


def test_variable_attributes_keyed_by_qualified_name(sample_nc):
    """variable_attributes keys by qualified name, including nested group variables."""
    with NetCDF(sample_nc) as reader:
        attrs = reader.variable_attributes()

    assert "gagecal/q" in attrs


def test_variable_attributes_include_fill_value_and_units(sample_nc):
    """A variable's attributes include long_name, units, and the _FillValue."""
    with NetCDF(sample_nc) as reader:
        stage_attrs = reader.variable_attributes()["stage"]

    assert "long_name" in stage_attrs
    assert "units" in stage_attrs
    assert "_FillValue" in stage_attrs
    assert stage_attrs["units"] == "m"
    assert float(stage_attrs["_FillValue"]) == -9999.0


def test_global_attributes_contain_root_attrs(sample_nc):
    """global_attributes returns the root dataset attributes."""
    with NetCDF(sample_nc) as reader:
        globals_ = reader.global_attributes()

    assert "Conventions" in globals_
    assert "title" in globals_
    assert globals_["Conventions"] == "CF-1.7"


def test_global_attributes_exclude_group_and_variable_attrs(sample_nc):
    """global_attributes is root-only: no group-level attrs, no variable names."""
    with NetCDF(sample_nc) as reader:
        globals_ = reader.global_attributes()

    assert "group_note" not in globals_
    assert "stage" not in globals_


def test_lazy_open_close_and_reopen(sample_nc):
    """The handle opens lazily on first .fp access, closes idempotently, and reopens."""
    reader = NetCDF(sample_nc)
    assert reader._fp is None

    assert reader.fp.isopen() is True

    reader.close()
    reader.close()
    assert reader._fp is None or not reader._fp.isopen()

    assert reader.fp.isopen() is True
    reader.close()


def test_context_manager_closes_on_exit(sample_nc):
    """The context manager closes the underlying dataset on block exit."""
    with NetCDF(sample_nc) as reader:
        ds = reader.fp
        assert ds.isopen() is True

    assert ds.isopen() is False


def test_missing_file_raises(tmp_path):
    """Accessing .fp for a nonexistent file raises an OSError."""
    reader = NetCDF(tmp_path / "does_not_exist.nc")

    with pytest.raises(OSError):
        _ = reader.fp


def test_numpy_to_token_round_trip():
    """numpy_to_token inverts the TOKEN_TO_NUMPY map for every DataType token."""
    for token in get_args(DataType):
        assert numpy_to_token(NetCDF.TOKEN_TO_NUMPY[token]) == token


def test_token_map_matches_model_vocabulary():
    """TOKEN_TO_NUMPY stays in sync with the DataType Literal vocabulary."""
    assert set(NetCDF.TOKEN_TO_NUMPY) == set(get_args(DataType))


def test_numpy_to_token_is_byte_order_agnostic():
    """numpy_to_token ignores byte order, mapping both endiannesses to the same token."""
    assert numpy_to_token(np.dtype(">f8")) == "f8"
    assert numpy_to_token(np.dtype("<f8")) == "f8"


def test_numpy_to_token_raises_on_vlen_string():
    """The vlen string type (Python str) is rejected: it is not in the contract vocabulary."""
    with pytest.raises(ValueError):
        numpy_to_token(str)
