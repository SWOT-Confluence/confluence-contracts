"""Tests for the structural validator (cit.validation).

Covers the P1-6 acceptance criteria against real (tiny) NetCDF files rather than fakes, so the
dtype tokens and dimension tuples under test are the ones netCDF4 actually reports:

- a declared variable absent from the file -> FAIL when required, WARN when optional;
- a wrong dtype -> FAIL, a wrong dimension list -> FAIL, and the two reported independently;
- a file variable the contract does not declare -> WARN (drift), asymmetric by design;
- everything agreeing -> PASSED.

Dimensions get the same existence treatment; their *sizes* are deliberately not compared, since
they vary per run. ``_partition`` is exercised directly because both directions of every
comparison flow through it.
"""

from pathlib import Path

import netCDF4 as nc
import pytest

from cit.models import Produces
from cit.report import FindingStatus, FindingType
from cit.result import NetcdfResult
from cit.validation import ContractValidator, Validator, ValidatorContext, _partition

MODULE = "momma"
FILEPATH = "flpe/momma/{reach_id}_momma.nc"

# One declared f8 variable over nt — the smallest contract that still exercises every check.
STAGE_ONLY = {"stage": {"dtype": "f8", "dimensions": ["nt"], "required": True}}


def _write_nc(path: Path, dimensions: dict[str, int], variables: dict[str, tuple]) -> None:
    """Write a NetCDF file with the given dimensions and variables.

    Args:
        path: Where to write the ``.nc`` file.
        dimensions: Dimension sizes as ``{name: size}``.
        variables: Variables as ``{name: (dtype_token, dim_names)}``.
    """
    ds = nc.Dataset(path, "w")
    for name, size in dimensions.items():
        ds.createDimension(name, size)
    for name, (dtype, dims) in variables.items():
        ds.createVariable(name, dtype, dims)
    ds.close()


def _contract(variables: dict, dimensions: list[str] | None = None) -> Produces:
    """Build a Produces contract from raw variable/dimension declarations."""
    return Produces.model_validate(
        {
            "filepath": FILEPATH,
            "dimensions": ["nt"] if dimensions is None else dimensions,
            "variables": variables,
        }
    )


@pytest.fixture
def validate(tmp_path):
    """Return a callable that validates a contract against a freshly written NetCDF file."""

    def _validate(contract, dimensions, variables):
        path = tmp_path / "result.nc"
        _write_nc(path, dimensions, variables)
        with NetcdfResult(str(path)) as result:
            context = ValidatorContext(MODULE, contract, [], result)
            return ContractValidator().validate(context)

    return _validate


def _for(findings, component):
    """Every finding about one component."""
    return [finding for finding in findings if finding.component == component]


def test_matching_contract_passes(validate):
    """A file matching its contract yields PASSED for the dimension and the variable."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 3}, {"stage": ("f8", ("nt",))})

    assert [f.type for f in findings] == [FindingType.PASSED, FindingType.PASSED]
    assert {f.status for f in findings} == {FindingStatus.INFO}
    assert {f.component for f in findings} == {"nt", "stage"}


def test_missing_required_variable_fails(validate):
    """A declared required variable absent from the file is a FAIL."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 3}, {})

    (stage,) = _for(findings, "stage")
    assert stage.type is FindingType.MISSING
    assert stage.status is FindingStatus.FAIL


def test_missing_optional_variable_warns(validate):
    """A declared optional variable absent from the file warns rather than fails."""
    contract = _contract({"stage": {"dtype": "f8", "dimensions": ["nt"], "required": False}})
    findings = validate(contract, {"nt": 3}, {})

    (stage,) = _for(findings, "stage")
    assert stage.type is FindingType.MISSING
    assert stage.status is FindingStatus.WARN


def test_undeclared_variable_warns(validate):
    """A file variable the contract does not declare is drift: WARN, never FAIL."""
    findings = validate(
        _contract(STAGE_ONLY),
        {"nt": 3},
        {"stage": ("f8", ("nt",)), "width": ("f8", ("nt",))},
    )

    (width,) = _for(findings, "width")
    assert width.type is FindingType.EXTRA
    assert width.status is FindingStatus.WARN


def test_wrong_dtype_fails(validate):
    """A variable whose dtype differs from the contract is a FAIL naming both dtypes."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 3}, {"stage": ("i4", ("nt",))})

    (stage,) = _for(findings, "stage")
    assert stage.type is FindingType.DIFFERENT
    assert stage.status is FindingStatus.FAIL
    assert "f8" in stage.message and "i4" in stage.message


def test_wrong_dimensions_fails(validate):
    """A variable indexed by different dimensions than declared is a FAIL."""
    contract = _contract(STAGE_ONLY, dimensions=["nt", "nx"])
    findings = validate(contract, {"nt": 3, "nx": 2}, {"stage": ("f8", ("nt", "nx"))})

    (stage,) = _for(findings, "stage")
    assert stage.type is FindingType.DIFFERENT
    assert stage.status is FindingStatus.FAIL


def test_dimension_order_is_significant(validate):
    """Declared [nx, nt] does not match a file's (nt, nx): order changes how consumers index."""
    contract = _contract(
        {"stage": {"dtype": "f8", "dimensions": ["nx", "nt"], "required": True}},
        dimensions=["nt", "nx"],
    )
    findings = validate(contract, {"nt": 3, "nx": 2}, {"stage": ("f8", ("nt", "nx"))})

    (stage,) = _for(findings, "stage")
    assert stage.type is FindingType.DIFFERENT
    assert stage.status is FindingStatus.FAIL


def test_dimension_sizes_are_not_compared(validate):
    """The contract declares no sizes, so a different nt is not a finding."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 143}, {"stage": ("f8", ("nt",))})

    assert {f.type for f in findings} == {FindingType.PASSED}


def test_dtype_and_dimensions_reported_independently(validate):
    """A variable wrong in both dtype and dimensions reports both, not just the first."""
    contract = _contract(STAGE_ONLY, dimensions=["nt", "nx"])
    findings = validate(contract, {"nt": 3, "nx": 2}, {"stage": ("i4", ("nt", "nx"))})

    stage = _for(findings, "stage")
    assert len(stage) == 2
    assert all(f.type is FindingType.DIFFERENT for f in stage)


def test_missing_dimension_fails(validate):
    """A declared dimension absent from the file is a FAIL."""
    contract = _contract(STAGE_ONLY, dimensions=["nt", "nx"])
    findings = validate(contract, {"nt": 3}, {"stage": ("f8", ("nt",))})

    (nx,) = _for(findings, "nx")
    assert nx.type is FindingType.MISSING
    assert nx.status is FindingStatus.FAIL


def test_undeclared_dimension_warns(validate):
    """A file dimension the contract does not declare is drift: WARN."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 3, "nx": 2}, {"stage": ("f8", ("nt",))})

    (nx,) = _for(findings, "nx")
    assert nx.type is FindingType.EXTRA
    assert nx.status is FindingStatus.WARN


def test_findings_carry_module_and_filepath(validate):
    """Every finding names the module and the produced file it came from."""
    findings = validate(_contract(STAGE_ONLY), {"nt": 3}, {})

    assert all(f.module_name == MODULE for f in findings)
    assert all(f.filepath == FILEPATH for f in findings)


def test_grouped_variables_qualify_nested_names(validate):
    """A variable inside a group arrives group-qualified, so it reads as undeclared drift."""
    findings = validate(
        _contract(STAGE_ONLY),
        {"nt": 3},
        {"stage": ("f8", ("nt",)), "gagecal/q": ("f8", ("nt",))},
    )

    (nested,) = _for(findings, "gagecal/q")
    assert nested.type is FindingType.EXTRA


@pytest.mark.parametrize(
    ("contract_names", "result_names", "expected"),
    [
        (["a", "b"], ["b", "c"], (["a"], ["c"], ["b"])),
        ([], ["a"], ([], ["a"], [])),
        (["a"], [], (["a"], [], [])),
        ([], [], ([], [], [])),
    ],
)
def test_partition_splits_both_directions(contract_names, result_names, expected):
    """_partition returns (missing, extra, common) from one pair of set differences."""
    assert _partition(contract_names, result_names) == expected


def test_partition_sorts_each_group():
    """Each group is sorted so report output does not depend on set iteration order."""
    missing, extra, common = _partition(["d", "b", "z"], ["z", "a", "c"])

    assert missing == ["b", "d"]
    assert extra == ["a", "c"]
    assert common == ["z"]


def test_partition_accepts_mappings():
    """A mapping may be passed for either side; iterating one yields its keys."""
    assert _partition({"a": 1, "b": 2}, {"b": 3}) == (["a"], [], ["b"])


def test_discover_instantiates_contract_validator():
    """ContractValidator registers itself, so discover() returns an instance of it."""
    validators = Validator.discover()

    assert any(isinstance(validator, ContractValidator) for validator in validators)
