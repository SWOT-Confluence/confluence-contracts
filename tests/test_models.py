"""Tests for the Pydantic v2 contract models (cit.models).

These are a TDD spec for issue P1-2. They cover two of the issue's acceptance criteria:

- AC1: a valid contract dict validates, defaults are applied, and the shipped bundled
  contract loads (the latter xfails while ``momma.yml`` is still a stub).
- AC2: malformed contracts raise :class:`pydantic.ValidationError` — unknown keys,
  wrong types, out-of-set dtype tokens, missing required fields, and inverted bounds.

Each invalid-input test starts from the ``valid_contract`` fixture and mutates exactly one
field so the raised error is attributable to that single change.
"""

from importlib.resources import files

import pytest
import yaml
from pydantic import ValidationError

from cit.models import Contract, VariableAttrs


def test_minimal_valid_contract_validates(valid_contract):
    """A hand-built valid contract validates and applies the ``required`` default."""
    del valid_contract["module"]["produces"][0]["variables"]["stage"]["required"]

    contract = Contract.model_validate(valid_contract)

    variable = contract.module.produces[0].variables["stage"]
    assert variable.dtype == "f8"
    assert variable.required is True


@pytest.mark.xfail(reason="momma.yml is still a stub — no contract body yet", strict=False)
def test_bundled_momma_contract_validates():
    """The shipped momma.yml resource loads and validates via importlib.resources."""
    text = files("cit.resources").joinpath("contracts", "momma.yml").read_text()
    data = yaml.safe_load(text)

    Contract.model_validate(data)


def test_unknown_key_rejected(valid_contract):
    """An unexpected/typo key is rejected because every model forbids extras."""
    valid_contract["module"]["produces"][0]["dimenions"] = ["nt"]

    with pytest.raises(ValidationError, match="dimenions"):
        Contract.model_validate(valid_contract)


def test_wrong_type_for_dtype_rejected(valid_contract):
    """A non-string dtype value fails validation against the dtype Literal."""
    valid_contract["module"]["produces"][0]["variables"]["stage"]["dtype"] = 8

    with pytest.raises(ValidationError):
        Contract.model_validate(valid_contract)


def test_dtype_outside_literal_rejected(valid_contract):
    """A dtype token outside the allowed Literal set is rejected."""
    valid_contract["module"]["produces"][0]["variables"]["stage"]["dtype"] = "float64"

    with pytest.raises(ValidationError):
        Contract.model_validate(valid_contract)


def test_missing_required_field_rejected(valid_contract):
    """Dropping a required top-level field (source) raises a validation error."""
    del valid_contract["source"]

    with pytest.raises(ValidationError, match="source"):
        Contract.model_validate(valid_contract)


def test_valid_min_greater_than_valid_max_rejected(valid_contract):
    """VariableAttrs rejects a variable whose valid_min exceeds valid_max."""
    attrs = valid_contract["module"]["produces"][0]["variables"]["stage"]["attrs"]
    attrs["valid_min"] = 1000.0
    attrs["valid_max"] = -1000.0

    with pytest.raises(ValidationError, match="valid_min"):
        Contract.model_validate(valid_contract)


def test_variable_attrs_bounds_validator_direct():
    """The bounds check triggers when VariableAttrs is validated on its own."""
    with pytest.raises(ValidationError, match="valid_min"):
        VariableAttrs.model_validate(
            {
                "long_name": "water surface elevation",
                "units": "m",
                "valid_min": 5.0,
                "valid_max": 1.0,
                "coverage_content_type": "physicalMeasurement",
            }
        )
