"""Tests for resolving ``cit parse``'s result files to modules and picking the rules parser."""

import argparse
from pathlib import Path

import pytest

from cit.__main__ import _pair, build_parser
from cit.orchestrate import Orchestrate
from cit.parse import OutputRulesParser, RulesParser

WORKBOOK = "docs/sos-dataset/sos_metadata.xlsx"
MOMMA = "/mnt/data/flpe/momma/12590000211_momma.nc"
METROMAN = "/mnt/data/flpe/metroman/12590000211_metroman.nc"


# --- _pair splitter (strict MODULE=PATH) ----------------------------------------------


def test_pair_accepts_tagged_value():
    """A tagged MODULE=PATH value is split into (name, path)."""
    assert _pair(f"momma={MOMMA}") == ("momma", MOMMA)


def test_pair_rejects_bare_path():
    """A bare path with no = is rejected with an informative message."""
    with pytest.raises(argparse.ArgumentTypeError, match="expected MODULE=VALUE"):
        _pair(MOMMA)


def test_pair_rejects_empty_name():
    """A value starting with = (no module name before it) is rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match="expected MODULE=VALUE"):
        _pair(f"={MOMMA}")


def test_pair_rejects_empty_value():
    """A value ending with = (no path after it) is rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match="expected MODULE=VALUE"):
        _pair("momma=")


# --- argparse wiring ------------------------------------------------------------------


def test_the_worked_example_parses():
    args = build_parser().parse_args(
        [
            "parse",
            "--module-file",
            f"momma={MOMMA}",
            "--module-file",
            f"metroman={METROMAN}",
            "--rule-file",
            f"output={WORKBOOK}",
        ]
    )
    assert args.module_file == [("momma", MOMMA), ("metroman", METROMAN)]
    assert args.rule_file == [("output", WORKBOOK)]
    assert args.strict is False


def test_rule_file_accepts_tagged_value():
    """--rule-file RULES=PATH is accepted and stored as a list of one (name, path) pair."""
    args = build_parser().parse_args(["parse", "--rule-file", f"output={WORKBOOK}"])
    assert args.rule_file == [("output", WORKBOOK)]


def test_untagged_module_file_is_rejected():
    """A bare path on --module-file is rejected by argparse."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--module-file", MOMMA])


def test_untagged_rule_file_is_rejected():
    """A bare path on --rule-file is rejected by argparse."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--rule-file", WORKBOOK])


def test_rules_is_gone():
    """The --rules flag no longer exists; an unknown flag raises SystemExit."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--rules", "output"])


def test_sos_group_is_gone():
    """The group is the module name, so it is never supplied separately."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse", "--sos-group", "momma"])


def test_module_file_repeats_accumulate():
    """Repeating --module-file with the same module name accumulates both entries."""
    momma2 = "/mnt/data/flpe/momma/74291800011_momma.nc"
    args = build_parser().parse_args(
        [
            "parse",
            "--module-file",
            f"momma={MOMMA}",
            "--module-file",
            f"momma={momma2}",
        ]
    )
    assert args.module_file == [("momma", MOMMA), ("momma", momma2)]


def test_rule_file_duplicate_name_errors():
    """Repeating --rule-file with the same rules name is rejected by argparse."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "parse",
                "--rule-file",
                f"output={WORKBOOK}",
                "--rule-file",
                "output=other.xlsx",
            ]
        )


def test_module_file_default_is_not_shared_across_calls():
    """The classic action='append' + mutable default trap: default must not accumulate."""
    first = build_parser().parse_args(["parse", "--module-file", f"momma={MOMMA}"])
    second = build_parser().parse_args(["parse"])
    assert first.module_file == [("momma", MOMMA)]
    assert second.module_file is None


# --- the whole parse ------------------------------------------------------------------


def test_parse_resolves_the_worked_example_against_the_workbook():
    targets = Orchestrate().parse([("momma", MOMMA), ("metroman", METROMAN)], WORKBOOK)
    assert [t.module for t in targets] == ["metroman", "momma"]
    assert all(isinstance(t.rules, OutputRulesParser) for t in targets)
    assert {t.rules for t in targets} == {targets[0].rules}, "one parser shared by the run"


def test_parse_groups_several_files_under_one_module():
    targets = Orchestrate().parse(
        [("momma", MOMMA), ("momma", "/mnt/data/flpe/momma/74291800011_momma.nc")], WORKBOOK
    )
    assert len(targets) == 1
    assert targets[0].module == "momma"
    assert len(targets[0].module_files) == 2


def test_parse_accepts_an_explicit_module_name():
    targets = Orchestrate().parse([("momma", "/mnt/data/anything.nc")], WORKBOOK)
    assert targets[0].module == "momma"
    assert targets[0].module_files == (Path("/mnt/data/anything.nc"),)


def test_parse_without_a_workbook_warns_and_produces_no_rules(caplog):
    targets = Orchestrate().parse([("momma", MOMMA)])
    assert targets[0].module == "momma"
    assert targets[0].rules is None
    assert "no --rule-file" in caplog.text


def test_parse_without_a_workbook_is_an_error_under_strict():
    with pytest.raises(ValueError, match="--strict requires a rules source"):
        Orchestrate().parse([("momma", MOMMA)], strict=True)


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
        Orchestrate().parse([("momma", MOMMA)], WORKBOOK, "nope")


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
