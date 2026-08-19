"""Tests for the cit command-line interface."""

import pytest

from cit.__main__ import build_parser, main
from cit.report import Finding, FindingStatus, FindingType, Report


@pytest.mark.parametrize("command", ["validate", "parse"])
def test_parser_accepts_subcommand(command):
    args = build_parser().parse_args([command])
    assert args.command == command


def test_validate_parses_with_no_arguments():
    """``cit validate`` alone still parses -- nothing is required=True."""
    args = build_parser().parse_args(["validate"])
    assert args.module is None
    assert args.results is None
    assert args.strict is False
    assert args.show_passed is False
    assert args.report is None
    assert args.csv is None


def test_validate_module_repeats():
    """--module is repeatable and accumulates in order given."""
    args = build_parser().parse_args(
        ["validate", "--module", "momma", "--module", "neobam"]
    )
    assert args.module == ["momma", "neobam"]


def test_validate_module_default_is_not_shared_across_calls():
    """The classic action='append' + mutable default trap: default must not accumulate."""
    first = build_parser().parse_args(["validate", "--module", "momma"])
    second = build_parser().parse_args(["validate"])
    assert first.module == ["momma"]
    assert second.module is None


def test_validate_new_flags_parse():
    """--results, --strict, --show-passed, --report, and --csv all parse."""
    args = build_parser().parse_args(
        [
            "validate",
            "--results",
            "/mnt/data",
            "--strict",
            "--show-passed",
            "--report",
            "report.txt",
            "--csv",
            "findings.csv",
        ]
    )
    assert args.results == "/mnt/data"
    assert args.strict is True
    assert args.show_passed is True
    assert args.report == "report.txt"
    assert args.csv == "findings.csv"


def test_verbose_parses_before_and_after_subcommand():
    """Both 'cit -v validate' and 'cit validate -v' parse and set verbose True."""
    before = build_parser().parse_args(["-v", "validate"])
    after = build_parser().parse_args(["validate", "-v"])
    assert before.verbose is True
    assert after.verbose is True


def test_verbose_defaults_false():
    """Without -v, verbose defaults to False regardless of position."""
    args = build_parser().parse_args(["validate"])
    assert args.verbose is False


def test_validate_requires_results(capsys):
    """``cit validate`` with no --results fails clearly, not with a traceback."""
    args = build_parser().parse_args(["validate"])
    with pytest.raises(SystemExit):
        args.func(args)


def test_main_exits_1_when_report_holds_a_fail(monkeypatch):
    """main() propagates the report's exit code: SystemExit(1) when any finding is FAIL."""
    finding = Finding(
        type=FindingType.MISSING,
        status=FindingStatus.FAIL,
        module_name="momma",
        component="stage",
        filepath="flpe/momma/{reach_id}_momma.nc",
        validation="contract",
        check="variable",
    )
    report = Report([finding])

    class _StubOrchestrate:
        def __init__(self, data_mount):
            self.data_mount = data_mount

        def run(self, strict=False, modules=None, *, show_passed=False):
            return report

    monkeypatch.setattr("cit.__main__.Orchestrate", _StubOrchestrate)
    monkeypatch.setattr("sys.argv", ["cit", "validate", "--results", "/mnt/data"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 1


def test_main_exits_0_when_report_has_no_fail(monkeypatch, capsys):
    """main() exits 0 for a WARN-only report, and the report text lands on stdout."""
    finding = Finding(
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        module_name="momma",
        component="extra_var",
        filepath="flpe/momma/{reach_id}_momma.nc",
        validation="contract",
        check="variable",
    )
    report = Report([finding])

    class _StubOrchestrate:
        def __init__(self, data_mount):
            self.data_mount = data_mount

        def run(self, strict=False, modules=None, *, show_passed=False):
            return report

    monkeypatch.setattr("cit.__main__.Orchestrate", _StubOrchestrate)
    monkeypatch.setattr("sys.argv", ["cit", "validate", "--results", "/mnt/data"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "extra_var" in captured.out
    assert captured.err == ""
