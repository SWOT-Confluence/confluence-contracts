"""Tests for resolving ``cit parse``'s result files to modules and picking the rules parser."""

import argparse
import logging
from pathlib import Path

import pytest

from cit.__main__ import _pair, build_parser
from cit.orchestrate import Orchestrate
from cit.parse import ContractParser, OutputRulesParser, ParsePlan, RulesParser

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


def test_parse_returns_a_parse_plan():
    """Orchestrate.parse returns a ParsePlan."""
    plan = Orchestrate().parse({"momma": [MOMMA]}, {"output": WORKBOOK})
    assert isinstance(plan, ParsePlan)


def test_parse_plan_contracts_keyed_by_module():
    """ParsePlan.contracts holds one ContractParser per module, keyed by name."""
    plan = Orchestrate().parse({"momma": [MOMMA], "metroman": [METROMAN]}, {"output": WORKBOOK})
    assert set(plan.contracts) == {"momma", "metroman"}
    assert isinstance(plan.contracts["momma"], ContractParser)
    assert plan.contracts["momma"].module == "momma"


def test_parse_plan_rules_keyed_by_name():
    """ParsePlan.rules holds one RulesParser per rules source, keyed by the registered name."""
    plan = Orchestrate().parse({"momma": [MOMMA]}, {"output": WORKBOOK})
    assert set(plan.rules) == {"output"}
    assert isinstance(plan.rules["output"], OutputRulesParser)


def test_parse_plan_both_is_sorted_intersection():
    """ParsePlan.both returns the sorted intersection of contracts and rules keys."""
    plan = Orchestrate().parse(
        {"momma": [MOMMA], "metroman": [METROMAN], "output": ["/mnt/data/output.nc"]},
        {"output": WORKBOOK},
    )
    assert plan.both == ["output"]


def test_parse_plan_both_is_empty_when_no_rules():
    """ParsePlan.both is empty when no rule files are given."""
    plan = Orchestrate().parse({"momma": [MOMMA]})
    assert plan.both == []


def test_parse_groups_multiple_files_under_one_module():
    """Passing several paths under the same module key groups them in one ContractParser."""
    momma2 = "/mnt/data/flpe/momma/74291800011_momma.nc"
    plan = Orchestrate().parse({"momma": [MOMMA, momma2]})
    assert len(plan.contracts) == 1
    assert plan.contracts["momma"].module_files == (Path(MOMMA), Path(momma2))


def test_parse_without_rule_file_warns(caplog):
    """A parse with no rule files logs a warning about missing SoS metadata."""
    with caplog.at_level(logging.WARNING, logger="cit.orchestrate"):
        Orchestrate().parse({"momma": [MOMMA]})
    assert "no --rule-file" in caplog.text


def test_parse_with_strict_and_no_rule_file_errors():
    """Under --strict, a parse with no rule files raises ValueError."""
    with pytest.raises(ValueError, match="--strict requires a rules source"):
        Orchestrate().parse({"momma": [MOMMA]}, strict=True)


def test_parse_rejects_unregistered_rules_name():
    """An unregistered rules name raises ValueError naming the registered alternatives."""
    with pytest.raises(ValueError, match="no rules parser is registered as 'nope'"):
        Orchestrate().parse({"momma": [MOMMA]}, {"nope": WORKBOOK})


def test_parse_logs_contracts_rules_both(caplog):
    """Orchestrate.parse logs three summary lines: contracts, rules, both."""
    with caplog.at_level(logging.INFO, logger="cit.orchestrate"):
        Orchestrate().parse(
            {"momma": [MOMMA], "metroman": [METROMAN], "output": ["/mnt/data/output.nc"]},
            {"output": WORKBOOK},
        )
    assert "contracts:" in caplog.text
    assert "'metroman'" in caplog.text
    assert "'momma'" in caplog.text
    assert "rules:" in caplog.text
    assert "'output' (OutputRulesParser)" in caplog.text
    assert "both:" in caplog.text
    assert "'output'" in caplog.text


def test_parse_contract_parsers_are_not_invoked():
    """ContractParser instances are constructed with module and files; parse() is not called."""
    plan = Orchestrate().parse({"momma": [MOMMA]}, {"output": WORKBOOK})
    assert plan.contracts["momma"].module == "momma"
    assert plan.contracts["momma"].module_files == (Path(MOMMA),)


def test_parse_cli_requires_module_file():
    """``cit parse`` with no --module-file exits with a clear message."""
    args = build_parser().parse_args(["parse"])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--module-file" in str(excinfo.value)


def test_parse_cli_translates_unregistered_rules_name_to_system_exit():
    """An unregistered rules name in --rule-file becomes a SystemExit with a clear message."""
    args = build_parser().parse_args(
        ["parse", "--module-file", f"momma={MOMMA}", "--rule-file", f"nope={WORKBOOK}"]
    )
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "no rules parser is registered as 'nope'" in str(excinfo.value)


def test_parse_cli_returns_0_on_success():
    """``cit parse`` returns 0 when the parse plan is built successfully."""
    args = build_parser().parse_args(
        ["parse", "--module-file", f"momma={MOMMA}", "--rule-file", f"output={WORKBOOK}"]
    )
    assert args.func(args) == 0


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
