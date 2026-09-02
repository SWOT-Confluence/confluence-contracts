"""Tests for drafting a contract from one exemplar result file (cit.parse.ContractParser).

MetroMan is the shape under test: it names each group after the reach set it covers, so a
file's group names -- and how many it has -- belong to that file rather than to the interface.
Those groups collapse into one ``{reach_set}`` declaration, which ``ContractValidator`` resolves
back per file. The two round-trip tests are what prove those halves agree.
"""

import logging
from pathlib import Path

import netCDF4 as nc
import pytest

from cit.contract import Produces
from cit.parse import ContractParser, RepoConfig
from cit.report import FindingStatus
from cit.result import NetcdfResult
from cit.validation import ContractValidator, ValidatorContext

MODULE = "metroman"
FILEPATH = "flpe/metroman/{reach_id}_metroman.nc"
SOURCE = {
    "repo": "metroman",
    "github_username": "SWOT-Confluence",
    "branch": "main",
    "commit": "69e8a96e01c3e4064c8c0c6a828afc6fcb5c1406",
    "image_tag": "metroman:latest",
}

# Two reach-set groups plus the fixed 'average' group, as a real metroman file holds them.
GROUPS = {
    "average": {"allq": ("f8", ("nt",)), "A0hat": ("f8", ())},
    "12780800021-12780800011": {"allq": ("f8", ("nt",)), "A0hat": ("f8", ())},
    "12780800031-12780800021-12780800011": {"allq": ("f8", ("nt",)), "A0hat": ("f8", ())},
}


def _write_nc(path: Path, groups: dict[str, dict], attrs: dict | None = None) -> None:
    """Write a grouped NetCDF file with a root nt dimension and variable.

    Args:
        path: Where to write the ``.nc`` file.
        groups: Variables per group, as ``{group: {name: (dtype_token, dim_names)}}``.
        attrs: Attributes to set, keyed by qualified variable name.
    """
    attrs = attrs or {}
    ds = nc.Dataset(path, "w")
    ds.createDimension("nt", 3)
    ds.createVariable("nt", "f8", ("nt",))
    for group_name, variables in groups.items():
        group = ds.createGroup(group_name)
        for name, (dtype, dims) in variables.items():
            # netCDF4 only accepts _FillValue at creation, never via setncattr.
            declared = dict(attrs.get(f"{group_name}/{name}", {}))
            fill = declared.pop("_FillValue", None)
            variable = group.createVariable(name, dtype, dims, fill_value=fill)
            for key, value in declared.items():
                variable.setncattr(key, value)
    ds.close()


def _config(**overrides) -> RepoConfig:
    """Build a metroman repo config, overriding any field."""
    return RepoConfig.model_validate({"filepath": FILEPATH, "source": SOURCE, **overrides})


def _parser(tmp_path: Path) -> ContractParser:
    """Build a ContractParser whose paths are unused -- _variables is driven directly."""
    return ContractParser(MODULE, tmp_path / "unused.nc", tmp_path / "unused.yml", "16.0")


@pytest.fixture
def draft(tmp_path):
    """Return a callable that drafts variables from a freshly written grouped file."""

    def _draft(config, groups=GROUPS, attrs=None):
        path = tmp_path / "result.nc"
        _write_nc(path, groups, attrs)
        with NetcdfResult(str(path)) as result:
            return _parser(tmp_path)._variables(result, config), str(path)

    return _draft


# --- collapsing file-specific groups --------------------------------------------------


def test_reach_set_groups_collapse_to_one_declaration(draft):
    """Every reach-set group's copy of a variable is drafted once, under the placeholder."""
    variables, _ = draft(_config(group_placeholder="reach_set", literal_groups=["average"]))

    assert sorted(variables) == [
        "average/A0hat",
        "average/allq",
        "nt",
        "{reach_set}/A0hat",
        "{reach_set}/allq",
    ]


def test_the_collapsed_declaration_keeps_dtype_and_dimensions(draft):
    """Collapsing changes the key, never the structure it declares."""
    variables, _ = draft(_config(group_placeholder="reach_set", literal_groups=["average"]))

    assert variables["{reach_set}/allq"].dtype == "f8"
    assert variables["{reach_set}/allq"].dimensions == ["nt"]
    assert variables["{reach_set}/A0hat"].dimensions == []
    assert variables["{reach_set}/allq"].required is True


def test_a_literal_group_is_declared_by_name(draft):
    """A group named in literal_groups is declared under its own name."""
    variables, _ = draft(_config(group_placeholder="reach_set", literal_groups=["average"]))

    assert "average/allq" in variables
    assert not any(key.startswith("{reach_set}/") and "average" in key for key in variables)


def test_without_a_placeholder_every_group_is_literal(draft):
    """A module whose group names are fixed drafts them as read -- the default."""
    variables, _ = draft(_config())

    assert "12780800021-12780800011/allq" in variables
    assert not any("{" in key for key in variables)


def test_a_flat_variable_is_never_rewritten(draft):
    """Only grouped variables are collapsed; a root variable is declared as read."""
    variables, _ = draft(_config(group_placeholder="reach_set", literal_groups=["average"]))

    assert "nt" in variables


def test_disagreeing_groups_warn_and_keep_the_first(draft, caplog):
    """Groups that collapse together but disagree are surfaced, not silently reconciled."""
    groups = {
        "average": {"allq": ("f8", ("nt",))},
        "12780800021": {"allq": ("f8", ("nt",))},
        "12780800031": {"allq": ("i4", ("nt",))},
    }
    with caplog.at_level(logging.WARNING):
        variables, _ = draft(
            _config(group_placeholder="reach_set", literal_groups=["average"]), groups=groups
        )

    assert variables["{reach_set}/allq"].dtype == "f8"
    assert "12780800031/allq" in caplog.text
    assert "{reach_set}/allq" in caplog.text


# --- attributes ------------------------------------------------------------------------


def test_long_name_falls_back_to_the_variable_name_not_its_group(draft):
    """A file carrying no long_name gets the variable's own name, not the templated key."""
    variables, _ = draft(_config(group_placeholder="reach_set", literal_groups=["average"]))

    assert variables["{reach_set}/allq"].attrs.long_name == "allq"


def test_a_declared_long_name_is_kept(draft):
    """The file's own long_name wins over the fallback."""
    attrs = {"12780800021-12780800011/allq": {"long_name": "all discharge estimates"}}
    variables, _ = draft(
        _config(group_placeholder="reach_set", literal_groups=["average"]), attrs=attrs
    )

    assert variables["{reach_set}/allq"].attrs.long_name == "all discharge estimates"


def test_fill_value_is_not_drafted_as_an_attribute(draft):
    """_FillValue is checked per type by RulesValidator, so it is dropped, not declared."""
    attrs = {"average/allq": {"_FillValue": -999.0, "units": "m^3/s"}}
    variables, _ = draft(
        _config(group_placeholder="reach_set", literal_groups=["average"]), attrs=attrs
    )

    assert variables["average/allq"].attrs.units == "m^3/s"
    assert "_FillValue" not in variables["average/allq"].attrs.model_dump()


# --- the round trip --------------------------------------------------------------------


def test_the_draft_validates_against_the_file_it_was_drafted_from(draft):
    """What the parser collapses, the validator resolves back -- no findings either way."""
    config = _config(group_placeholder="reach_set", literal_groups=["average"])
    variables, path = draft(config)

    produces = Produces(filepath=FILEPATH, dimensions=["nt"], variables=variables)
    with NetcdfResult(path) as result:
        findings = ContractValidator().validate(ValidatorContext(MODULE, produces, [], result))

    assert [f for f in findings if f.status is FindingStatus.FAIL] == []
    assert [f for f in findings if f.status is FindingStatus.WARN] == []


def test_the_draft_validates_against_a_file_with_different_groups(draft, tmp_path):
    """The point of the placeholder: the draft holds for differently named, fewer groups."""
    config = _config(group_placeholder="reach_set", literal_groups=["average"])
    variables, _ = draft(config)

    other = tmp_path / "other.nc"
    _write_nc(
        other,
        {
            "average": {"allq": ("f8", ("nt",)), "A0hat": ("f8", ())},
            "12797100101-12797100091": {"allq": ("f8", ("nt",)), "A0hat": ("f8", ())},
        },
    )

    produces = Produces(filepath=FILEPATH, dimensions=["nt"], variables=variables)
    with NetcdfResult(str(other)) as result:
        findings = ContractValidator().validate(ValidatorContext(MODULE, produces, [], result))

    assert [f for f in findings if f.status is FindingStatus.FAIL] == []
    assert [f for f in findings if f.status is FindingStatus.WARN] == []
