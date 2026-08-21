"""Tests for the SoS metadata-rules validator (cit.validation.RulesValidator).

Covers the P1-8 acceptance criteria against real (tiny) NetCDF files rather than fakes, so the
attribute values under test are the numpy scalars and byte strings netCDF4 actually hands back:

- a variable with no ``units`` -> WARN, and FAIL under ``--strict``;
- a ``_FillValue`` that does not match the canonical value for its dtype -> a violation;
- a complete variable -> clean.

The three whole-variable checks are exercised directly, because each has a distinct precondition:
``_check_required_attributes`` needs nothing, ``_check_valid_min_max`` needs two coercible bounds,
and ``_check_fill_value`` needs a declared fill attribute and a mapped dtype.
"""

from pathlib import Path

import netCDF4 as nc
import pytest

from cit.contract import Produces
from cit.report import FindingStatus, FindingType, ValidationSource
from cit.result import NetcdfResult
from cit.rules import MetadataRules
from cit.validation import (
    RulesValidator,
    Validator,
    ValidatorContext,
    _numeric,
    _Reporter,
    _same_fill,
)

MODULE = "output"
FILEPATH = "output/sos/{continent_id}_sword_v{number}_SOS_results.nc"

# The four canonical SoS fill values, matching the committed artifact.
FILL_VALUES = {"Float": -999999999999.0, "Int": -999, "Int9": -99999999, "Char": "x"}

# A fully documented variable: every required attribute present, bounds ordered.
COMPLETE_ATTRS = {
    "long_name": "water surface elevation",
    "units": "m",
    "coverage_content_type": "modelResult",
    "valid_min": -100.0,
    "valid_max": 10000.0,
}


def _write_nc(path: Path, variables: dict[str, tuple[str, dict]]) -> None:
    """Write a NetCDF file whose variables carry the given attributes.

    Args:
        path: Where to write the ``.nc`` file.
        variables: ``{name: (dtype_token, {attribute: value})}``. ``_FillValue`` is applied at
            creation time, since netCDF4 refuses to set it afterwards.
    """
    ds = nc.Dataset(path, "w")
    ds.createDimension("num_reaches", 3)
    for name, (dtype, attrs) in variables.items():
        fill = attrs.get("_FillValue")
        kwargs = {"fill_value": fill} if fill is not None else {}
        variable = ds.createVariable(name, dtype, ("num_reaches",), **kwargs)
        for attribute, value in attrs.items():
            if attribute != "_FillValue":
                variable.setncattr(attribute, value)
    ds.close()


def _rules(variable_attributes: dict | None = None, global_attributes: list | None = None):
    """Build a MetadataRules covering one group, with the canonical fill table."""
    return MetadataRules.model_validate(
        {
            "module_name": MODULE,
            "filepath": FILEPATH,
            "global_attributes": global_attributes or [],
            "variable_attributes": variable_attributes or {},
            "fill_values": FILL_VALUES,
        }
    )


@pytest.fixture
def reporter():
    """A _Reporter bound to this module and file, as a validator run would build."""
    return _Reporter(MODULE, FILEPATH, ValidationSource.METADATA)


@pytest.fixture
def validate(tmp_path):
    """Return a callable that runs RulesValidator over a freshly written NetCDF file."""

    def _validate(rules, variables, strict=False):
        path = tmp_path / "result.nc"
        _write_nc(path, variables)
        contract = Produces.model_validate({"filepath": FILEPATH, "variables": {}})
        with NetcdfResult(str(path)) as result:
            context = ValidatorContext(MODULE, contract, rules, result, strict)
            return RulesValidator().validate(context)

    return _validate


def _for(findings, component):
    """Every finding about one component."""
    return [finding for finding in findings if finding.component == component]


# --------------------------------------------------------------------------------------
# registration and the no-rules short circuit
# --------------------------------------------------------------------------------------


def test_discover_instantiates_rules_validator():
    """RulesValidator is registered by defining it, so discover() returns one."""
    assert any(isinstance(validator, RulesValidator) for validator in Validator.discover())


def test_module_without_rules_is_skipped(validate):
    """A module with no rules artifact produces no findings at all."""
    assert validate(None, {"stage": ("f8", COMPLETE_ATTRS)}) == []


# --------------------------------------------------------------------------------------
# required attributes -- the headline acceptance criterion
# --------------------------------------------------------------------------------------


def test_variable_without_units_warns(reporter):
    """A variable with no units is reported, even though the spreadsheet omits units too."""
    attrs = {k: v for k, v in COMPLETE_ATTRS.items() if k != "units"}

    findings = RulesValidator()._check_required_attributes(reporter, attrs, "momma/slope", False)

    assert [f.component for f in findings] == ["momma/slope.units"]
    assert findings[0].type is FindingType.MISSING
    assert findings[0].status is FindingStatus.WARN
    assert findings[0].message == "required by the SoS metadata spec"


def test_variable_without_units_fails_under_strict(reporter):
    """--strict turns the same finding into a failure."""
    attrs = {k: v for k, v in COMPLETE_ATTRS.items() if k != "units"}

    findings = RulesValidator()._check_required_attributes(reporter, attrs, "momma/slope", True)

    assert findings[0].status is FindingStatus.FAIL


def test_blank_required_attribute_counts_as_absent(reporter):
    """An empty units string is no more useful than no units at all."""
    findings = RulesValidator()._check_required_attributes(
        reporter, {**COMPLETE_ATTRS, "units": "   "}, "momma/slope", False
    )

    assert [f.component for f in findings] == ["momma/slope.units"]


def test_complete_variable_has_no_required_attribute_findings(reporter):
    """A variable carrying every mandatory attribute is clean."""
    assert (
        RulesValidator()._check_required_attributes(reporter, COMPLETE_ATTRS, "momma/stage", False)
        == []
    )


def test_required_attributes_cover_the_mandatory_contract_fields():
    """REQUIRED_ATTRS mirrors the VariableAttrs fields that have no default."""
    from cit.contract import VariableAttrs

    mandatory = {name for name, field in VariableAttrs.model_fields.items() if field.is_required()}

    assert set(RulesValidator.REQUIRED_ATTRS) == mandatory


# --------------------------------------------------------------------------------------
# valid_min / valid_max
# --------------------------------------------------------------------------------------


def test_ordered_bounds_pass(reporter):
    """valid_min below valid_max is reported as PASSED."""
    finding = RulesValidator()._check_valid_min_max(reporter, COMPLETE_ATTRS, "momma/stage", False)

    assert finding.type is FindingType.PASSED
    assert finding.status is FindingStatus.INFO


def test_equal_bounds_pass(reporter):
    """valid_min equal to valid_max is a legal single-valued range, not an inversion."""
    attrs = {"valid_min": 1.0, "valid_max": 1.0}

    finding = RulesValidator()._check_valid_min_max(reporter, attrs, "momma/seg", False)

    assert finding.type is FindingType.PASSED


def test_inverted_bounds_are_reported(reporter):
    """valid_min above valid_max is a violation naming both values."""
    attrs = {"valid_min": 10.0, "valid_max": 1.0}

    finding = RulesValidator()._check_valid_min_max(reporter, attrs, "momma/stage", False)

    assert finding.type is FindingType.DIFFERENT
    assert finding.status is FindingStatus.WARN
    assert finding.message == "(valid_min: 10.0) exceeds (valid_max: 1.0)"


def test_inverted_bounds_fail_under_strict(reporter):
    """--strict escalates an inverted range."""
    finding = RulesValidator()._check_valid_min_max(
        reporter, {"valid_min": 10.0, "valid_max": 1.0}, "momma/stage", True
    )

    assert finding.status is FindingStatus.FAIL


@pytest.mark.parametrize(
    "attrs",
    [
        {"valid_min": 0.0},
        {"valid_max": 1.0},
        {},
        {"valid_min": "inf; 9.99999999998E11", "valid_max": 1.0},
    ],
    ids=["min-only", "max-only", "neither", "non-numeric"],
)
def test_bounds_skipped_when_not_comparable(reporter, attrs):
    """A missing or non-numeric bound is skipped rather than guessed at."""
    assert RulesValidator()._check_valid_min_max(reporter, attrs, "validation/rmse", False) is None


# --------------------------------------------------------------------------------------
# fill values
# --------------------------------------------------------------------------------------


def test_canonical_float_fill_passes(reporter):
    """The canonical Float fill on an f8 variable passes."""
    attrs = {"_FillValue": -999999999999.0}

    finding = RulesValidator()._check_fill_value(
        reporter, attrs, "f8", FILL_VALUES, "momma/stage", False
    )

    assert finding.type is FindingType.PASSED


def test_wrong_fill_for_dtype_is_reported(reporter):
    """A fill value that is not canonical for the dtype is a violation naming both."""
    attrs = {"_FillValue": -999.0}

    finding = RulesValidator()._check_fill_value(
        reporter, attrs, "f8", FILL_VALUES, "momma/stage", False
    )

    assert finding.type is FindingType.DIFFERENT
    assert finding.status is FindingStatus.WARN
    assert "-999.0" in finding.message
    assert finding.component == "momma/stage._FillValue"


def test_wrong_fill_fails_under_strict(reporter):
    """--strict escalates a fill mismatch."""
    finding = RulesValidator()._check_fill_value(
        reporter, {"_FillValue": -999.0}, "f8", FILL_VALUES, "momma/stage", True
    )

    assert finding.status is FindingStatus.FAIL


def test_absent_fill_is_not_a_violation(reporter):
    """Declaring no fill value is not reportable: netCDF forbids _FillValue on VLEN types."""
    assert (
        RulesValidator()._check_fill_value(
            reporter, COMPLETE_ATTRS, "f8", FILL_VALUES, "momma/Q", False
        )
        is None
    )


@pytest.mark.parametrize("carrier", ["_FillValue", "missing_value", "fill"])
def test_every_fill_carrier_is_checked(reporter, carrier):
    """A fill declared as missing_value or the non-standard fill is checked like _FillValue."""
    finding = RulesValidator()._check_fill_value(
        reporter, {carrier: -999.0}, "f8", FILL_VALUES, "momma/stage", False
    )

    assert finding.type is FindingType.DIFFERENT
    assert finding.component == f"momma/stage.{carrier}"


@pytest.mark.parametrize("dtype", ["i4", "i8"])
@pytest.mark.parametrize("value", [-999, -99999999])
def test_integer_dtypes_accept_either_int_fill(reporter, dtype, value):
    """Int and Int9 are both acceptable: the spreadsheet's per-variable type column is dropped."""
    finding = RulesValidator()._check_fill_value(
        reporter, {"_FillValue": value}, dtype, FILL_VALUES, "validation/has_validation", False
    )

    assert finding.type is FindingType.PASSED


def test_char_fill_compares_across_bytes(reporter):
    """An S1 fill reads back as b"x" while the artifact holds "x"; they must compare equal."""
    finding = RulesValidator()._check_fill_value(
        reporter, {"_FillValue": b"x"}, "S1", FILL_VALUES, "validation/algo_names", False
    )

    assert finding.type is FindingType.PASSED


def test_unmapped_dtype_is_reported_as_skipped(reporter):
    """A dtype with no canonical fill says so as SKIPPED, rather than as a data disagreement."""
    finding = RulesValidator()._check_fill_value(
        reporter, {"_FillValue": 1}, "u1", FILL_VALUES, "postdiagnostics/flag", False
    )

    assert finding.type is FindingType.SKIPPED
    assert "no canonical fill value" in finding.message


def test_unmapped_dtype_is_never_escalated(reporter):
    """An unmodelled dtype is a gap in CIT, not in the data, so --strict never escalates it.

    SKIPPED/REPORT already means never-escalated -- not because ``--strict`` was consulted and
    declined, but because REPORT is not part of the pass/fail/warn ladder ``--strict`` promotes
    within at all.
    """
    finding = RulesValidator()._check_fill_value(
        reporter, {"_FillValue": 1}, "u1", FILL_VALUES, "postdiagnostics/flag", True
    )

    assert finding.status is FindingStatus.REPORT


def test_fill_types_cover_the_dtype_vocabulary():
    """Every DataType token maps to a fill type, so no declared dtype goes unchecked."""
    from typing import get_args

    from cit.contract import DataType

    assert set(RulesValidator.TOKEN_TO_FILL_TYPES) == set(get_args(DataType))


# --------------------------------------------------------------------------------------
# fill attributes must not double-report as attribute-level EXTRA
# --------------------------------------------------------------------------------------


def test_fill_attributes_are_not_reported_as_extra(validate):
    """A fill attribute is checked by value, not reported as an undeclared attribute."""
    rules = _rules({"momma": {"stage": {**COMPLETE_ATTRS}}})
    variables = {"momma/stage": ("f8", {**COMPLETE_ATTRS, "_FillValue": -999999999999.0})}

    findings = validate(rules, variables)
    extras = [f for f in findings if f.type is FindingType.EXTRA]

    assert [f.component for f in extras] == []


def test_non_fill_extra_attribute_is_still_reported(validate):
    """An attribute the spreadsheet does not model is still surfaced -- only fills are exempt."""
    rules = _rules({"momma": {"stage": {**COMPLETE_ATTRS}}})
    variables = {"momma/stage": ("f8", {**COMPLETE_ATTRS, "flag_values": "0 1"})}

    findings = validate(rules, variables)
    extras = [f for f in findings if f.type is FindingType.EXTRA]

    assert [f.component for f in extras] == ["momma/stage.flag_values"]


# --------------------------------------------------------------------------------------
# global attributes
# --------------------------------------------------------------------------------------


def test_missing_required_global_attribute_warns(validate):
    """A required global attribute the file omits is reported."""
    rules = _rules(global_attributes=["title", "product_version"])

    findings = validate(rules, {"momma/stage": ("f8", COMPLETE_ATTRS)})

    missing = [f for f in findings if f.type is FindingType.MISSING and f.component == "title"]
    assert len(missing) == 1
    assert missing[0].status is FindingStatus.WARN


def test_missing_required_global_attribute_fails_under_strict(validate):
    """--strict escalates a missing required global attribute."""
    rules = _rules(global_attributes=["title"])

    findings = validate(rules, {"momma/stage": ("f8", COMPLETE_ATTRS)}, strict=True)

    assert _for(findings, "title")[0].status is FindingStatus.FAIL


# --------------------------------------------------------------------------------------
# a clean file
# --------------------------------------------------------------------------------------


def test_complete_variable_produces_no_violations(validate):
    """A fully documented variable with a canonical fill yields only PASSED findings."""
    rules = _rules({"momma": {"stage": {**COMPLETE_ATTRS}}})
    variables = {"momma/stage": ("f8", {**COMPLETE_ATTRS, "_FillValue": -999999999999.0})}

    findings = validate(rules, variables)

    assert findings, "expected the PASSED findings, not an empty run"
    assert all(f.status is FindingStatus.INFO for f in findings)
    assert all(f.type is FindingType.PASSED for f in findings)


# --------------------------------------------------------------------------------------
# the comparison helpers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.5, 1.5), ("2.5", 2.5), (-999, -999.0), ("inf", float("inf")), ("x", None), (None, None)],
    ids=["float", "numeric-str", "int", "inf-str", "non-numeric", "none"],
)
def test_numeric_coerces_or_returns_none(value, expected):
    """_numeric converts anything float() accepts and returns None otherwise."""
    assert _numeric(value) == expected or (expected is None and _numeric(value) is None)


def test_numeric_handles_numpy_widths():
    """Every numpy scalar width coerces, including the ones that do not subclass int/float."""
    import numpy as np

    for value in (np.float64(1.5), np.float32(1.5), np.int64(-999), np.int32(-999)):
        assert _numeric(value) is not None


@pytest.mark.parametrize(
    ("actual", "expected", "same"),
    [
        (-999.0, -999, True),
        (b"x", "x", True),
        ("x", "x", True),
        (float("nan"), -999999999999.0, False),
        (-999.0, -99999999, False),
        ("x", -999, False),
    ],
    ids=["numeric-widths", "bytes-vs-str", "str", "nan", "wrong-number", "str-vs-number"],
)
def test_same_fill(actual, expected, same):
    """_same_fill compares across bytes, str and numeric forms; nan never matches."""
    assert _same_fill(actual, expected) is same
