"""Tests for resolving ``cit parse``'s result files to modules and picking the rules parser."""

from pathlib import Path

import pytest

from cit.__main__ import _module_file, build_parser
from cit.orchestrate import Orchestrate
from cit.parse import DEFAULT_RULE_NAME, OutputRulesParser, RulesParser

WORKBOOK = "docs/sos-dataset/sos_metadata.xlsx"
MOMMA = "/mnt/data/flpe/momma/12590000211_momma.nc"
METROMAN = "/mnt/data/flpe/metroman/12590000211_metroman.nc"


# --- --module-file values ------------------------------------------------------------


def test_a_bare_path_leaves_the_module_to_be_matched():
    assert _module_file(MOMMA) == (None, MOMMA)


def test_module_equals_path_states_the_module_outright():
    assert _module_file(f"momma={MOMMA}") == ("momma", MOMMA)


def test_an_equals_inside_a_path_is_not_a_module_name():
    """A templated or oddly named path keeps its = rather than losing its head to it."""
    assert _module_file("/mnt/data/a=b/12590000211_momma.nc") == (
        None,
        "/mnt/data/a=b/12590000211_momma.nc",
    )


# --- argparse wiring ------------------------------------------------------------------


def test_the_worked_example_parses():
    args = build_parser().parse_args(
        ["parse", "--module-file", MOMMA, "--module-file", METROMAN, "--rule-file", WORKBOOK]
    )
    assert args.module_file == [(None, MOMMA), (None, METROMAN)]
    assert args.rule_file == WORKBOOK
    assert args.rules == DEFAULT_RULE_NAME
    assert args.strict is False


def test_rule_file_is_one_workbook_not_one_per_module():
    """The workbook holds every module's tab, so --rule-file takes a single value."""
    args = build_parser().parse_args(["parse", "--rule-file", WORKBOOK])
    assert isinstance(args.rule_file, str)


def test_rules_only_accepts_a_registered_parser():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--rules", "momma"])


def test_sos_group_is_gone():
    """The group is the module name, so it is never supplied separately."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--sos-group", "momma"])


def test_module_file_default_is_not_shared_across_calls():
    first = build_parser().parse_args(["parse", "--module-file", MOMMA])
    second = build_parser().parse_args(["parse"])
    assert first.module_file == [(None, MOMMA)]
    assert second.module_file is None


# --- resolving a filename to a module -------------------------------------------------


def test_resolve_uses_the_contract_that_declares_the_filename():
    """contracts/momma.yml declares flpe/momma/{reach_id}_momma.nc, so momma claims the file."""
    assert Orchestrate()._resolve(MOMMA, []) == "momma"


def test_resolve_finds_a_module_its_filename_never_mentions():
    """'af_sword_v17_SOS_results.nc' says nothing about output -- output.yml's template does."""
    granule = "/mnt/data/output/sos/af_sword_v17_SOS_results.nc"
    assert Orchestrate()._resolve(granule, []) == "output"


def test_resolve_matches_a_published_granule_with_its_run_type_and_timestamps():
    granule = "/mnt/data/na_sword_v16_SOS_results.nc"
    assert Orchestrate()._resolve(granule, []) == "output"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("12590000211_metroman.nc", "metroman"),
        ("metroman.nc", "metroman"),
    ],
)
def test_resolve_falls_back_to_the_filename_suffix(filename, expected):
    """metroman has a workbook tab but no contract yet, so the suffix rule carries it."""
    assert Orchestrate()._resolve(f"/mnt/data/{filename}", ["momma", "metroman"]) == expected


def test_resolve_prefers_the_longest_suffix_match():
    """'metroman' must not lose to a shorter module whose name it ends with."""
    assert Orchestrate()._resolve("/mnt/data/123_metroman.nc", ["man", "metroman"]) == "metroman"


def test_resolve_refuses_to_invent_a_module_name():
    """Freehand inference would commit a contract under a name nobody chose."""
    with pytest.raises(ValueError, match="cannot tell which module produced 'mystery.nc'"):
        Orchestrate()._resolve("/mnt/data/mystery.nc", ["momma", "metroman"])


def test_resolve_error_names_both_lookups_and_the_explicit_form():
    with pytest.raises(ValueError, match="no contract declares a matching produces template"):
        Orchestrate()._resolve("/mnt/data/mystery.nc", ["momma"])
    with pytest.raises(ValueError, match="--module-file MODULE="):
        Orchestrate()._resolve("/mnt/data/mystery.nc", ["momma"])


# --- the whole parse ------------------------------------------------------------------


def test_parse_resolves_the_worked_example_against_the_workbook():
    targets = Orchestrate().parse([(None, MOMMA), (None, METROMAN)], WORKBOOK)
    assert [t.module for t in targets] == ["metroman", "momma"]
    assert all(isinstance(t.rules, OutputRulesParser) for t in targets)
    assert {t.rules for t in targets} == {targets[0].rules}, "one parser shared by the run"


def test_parse_groups_several_files_under_one_module():
    targets = Orchestrate().parse(
        [(None, MOMMA), (None, "/mnt/data/flpe/momma/74291800011_momma.nc")], WORKBOOK
    )
    assert len(targets) == 1
    assert targets[0].module == "momma"
    assert len(targets[0].module_files) == 2


def test_parse_resolves_a_momma_file_and_a_sos_granule_with_no_tags():
    """The asymmetry is gone: both sides of a real run resolve from their declared templates."""
    granule = "/mnt/data/output/sos/af_sword_v17_SOS_results.nc"
    targets = Orchestrate().parse([(None, MOMMA), (None, granule)], WORKBOOK)
    assert [t.module for t in targets] == ["momma", "output"]


def test_parse_accepts_an_explicit_module_name():
    targets = Orchestrate().parse([("momma", "/mnt/data/anything.nc")], WORKBOOK)
    assert targets[0].module == "momma"
    assert targets[0].module_files == (Path("/mnt/data/anything.nc"),)


def test_parse_without_a_workbook_matches_against_the_bundled_contracts(caplog):
    targets = Orchestrate().parse([(None, MOMMA)])
    assert targets[0].module == "momma"
    assert targets[0].rules is None
    assert "no --rule-file" in caplog.text


def test_parse_without_a_workbook_is_an_error_under_strict():
    with pytest.raises(ValueError, match="--strict requires a rules source"):
        Orchestrate().parse([(None, MOMMA)], strict=True)


def test_the_rules_source_covers_the_file_it_is_named_for():
    """'output' is the file, not a group -- root plus every group tab describes it."""
    targets = Orchestrate().parse([("output", "/mnt/data/na_SOS_results.nc")], WORKBOOK)
    assert isinstance(targets[0].rules, OutputRulesParser)


def test_parse_warns_when_a_module_is_in_neither_the_groups_nor_the_source(caplog):
    targets = Orchestrate().parse([("lakeflow", "/mnt/data/x.nc")], WORKBOOK)
    assert targets[0].rules is None
    assert "no rules for 'lakeflow'" in caplog.text


def test_parse_rejects_an_unregistered_rules_source():
    with pytest.raises(ValueError, match="no rules parser is registered as 'nope'"):
        Orchestrate().parse([(None, MOMMA)], WORKBOOK, "nope")


# --- the registry ---------------------------------------------------------------------


def test_output_is_the_only_registered_rules_source():
    assert RulesParser.names() == ["output"]
    assert RulesParser.get("output") is OutputRulesParser
    assert RulesParser.get("momma") is None


def test_the_output_parser_serves_every_module_in_the_workbook():
    """One workbook, one parser, one tab per module -- momma and metroman both go through it."""
    groups = OutputRulesParser(Path(WORKBOOK)).groups()
    assert {"momma", "metroman", "hivdi", "sad"} <= set(groups)
    assert "output" not in groups, "output is the file, described by the root tab"
    assert not {"root", "global_attributes", "fill_values", "README"} & set(groups)


def test_groups_ignores_underscore_prefixed_tabs():
    assert "_TEMPLATE" not in OutputRulesParser(Path(WORKBOOK)).groups()


def test_a_second_parser_cannot_steal_a_registered_rule_name():
    with pytest.raises(TypeError, match="already registered"):

        class Duplicate(RulesParser):
            rule_name = "output"

            def groups(self):
                return []

            def parse(self, *args, **kwargs):
                ...


def test_a_concrete_parser_must_declare_a_rule_name():
    with pytest.raises(TypeError, match="must set a class-level rule_name"):

        class Nameless(RulesParser):
            def groups(self):
                return []

            def parse(self, *args, **kwargs):
                ...
