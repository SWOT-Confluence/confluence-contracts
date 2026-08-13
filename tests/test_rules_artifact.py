"""Drift guard for the committed SoS metadata-rules artifact.

Loads src/cit/resources/rules/sos_results_rules.yml via importlib.resources and
asserts structural invariants. If this test fails, the artifact has been hand-edited
or regenerated incompletely — re-run tools/rules_convert.py to restore it.
"""

import importlib.resources

import pytest
import yaml


@pytest.fixture(scope="module")
def rules() -> dict:
    """Load the bundled rules artifact once for all tests in this module.

    Returns:
        Parsed YAML dict with keys global_attributes, variable_attributes,
        fill_values.
    """
    path = importlib.resources.files("cit.resources").joinpath("rules/sos_results_rules.yml")
    with importlib.resources.as_file(path) as p:
        return yaml.safe_load(p.read_text())


def test_top_level_keys(rules: dict) -> None:
    """Artifact has exactly the keys global_attributes, variable_attributes, fill_values."""
    assert set(rules.keys()) == {"global_attributes", "variable_attributes", "fill_values"}


def test_global_attributes_count(rules: dict) -> None:
    """global_attributes is a list of exactly 32 strings."""
    attrs = rules["global_attributes"]
    assert isinstance(attrs, list)
    assert len(attrs) == 32
    assert all(isinstance(a, str) for a in attrs)


def test_global_attributes_known_entries(rules: dict) -> None:
    """Spot-check: production_date, title, and date_created are all present."""
    attrs = rules["global_attributes"]
    assert "production_date" in attrs
    assert "title" in attrs
    assert "date_created" in attrs


def test_variable_attributes_groups(rules: dict) -> None:
    """variable_attributes has exactly 14 group keys."""
    groups = rules["variable_attributes"]
    assert isinstance(groups, dict)
    assert len(groups) == 14


def test_momma_variable_count(rules: dict) -> None:
    """variable_attributes['momma'] has exactly 36 variables."""
    momma = rules["variable_attributes"]["momma"]
    assert isinstance(momma, dict)
    assert len(momma) == 36


def test_momma_known_variable(rules: dict) -> None:
    """variable_attributes['momma']['stage'] exists and has the expected metadata keys."""
    stage = rules["variable_attributes"]["momma"]["stage"]
    assert isinstance(stage, dict)
    assert "long_name" in stage
    assert "units" in stage
    assert "coverage_content_type" in stage


def test_fill_values_keys(rules: dict) -> None:
    """fill_values has exactly the keys Float, Int, Int9, Char."""
    assert set(rules["fill_values"].keys()) == {"Float", "Int", "Int9", "Char"}


def test_fill_values_numeric(rules: dict) -> None:
    """fill_values['Float'] is a float or int (not a string); fill_values['Int'] == -999."""
    fill = rules["fill_values"]
    assert isinstance(fill["Float"], (float, int))
    assert fill["Int"] == -999
