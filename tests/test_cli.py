"""Tests for the cit command-line interface."""

import pytest

from cit.__main__ import build_parser, main
from cit.report import (
    DEFAULT_MAX_FILES,
    Check,
    Finding,
    FindingStatus,
    FindingType,
    Report,
    ValidationSource,
)


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
    assert args.show_files is False
    assert args.max_files is None
    assert args.report is None
    assert args.csv is None


def test_validate_module_repeats():
    """--module is repeatable and accumulates in order given."""
    args = build_parser().parse_args(["validate", "--module", "momma", "--module", "neobam"])
    assert args.module == ["momma", "neobam"]


def test_validate_module_default_is_not_shared_across_calls():
    """The classic action='append' + mutable default trap: default must not accumulate."""
    first = build_parser().parse_args(["validate", "--module", "momma"])
    second = build_parser().parse_args(["validate"])
    assert first.module == ["momma"]
    assert second.module is None


def test_validate_new_flags_parse():
    """--results, --strict, --show-passed, --show-files, --max-files, --report, --csv all parse."""
    args = build_parser().parse_args(
        [
            "validate",
            "--results",
            "/mnt/data",
            "--strict",
            "--show-passed",
            "--show-files",
            "--max-files",
            "3",
            "--report",
            "report.txt",
            "--csv",
            "findings.csv",
        ]
    )
    assert args.results == "/mnt/data"
    assert args.strict is True
    assert args.show_passed is True
    assert args.show_files is True
    assert args.max_files == 3
    assert args.report == "report.txt"
    assert args.csv == "findings.csv"


def test_validate_checks_defaults_to_all():
    """--checks defaults to 'all', so an unfiltered run needs no flag at all."""
    args = build_parser().parse_args(["validate"])
    assert args.checks == "all"


@pytest.mark.parametrize("source", list(ValidationSource))
def test_validate_checks_accepts_each_validation_source(source):
    """--checks accepts every ValidationSource member's value, not a hand-maintained list."""
    args = build_parser().parse_args(["validate", "--checks", source])
    assert args.checks == source


def test_validate_checks_rejects_an_invalid_value():
    """An unrecognized --checks value is rejected by argparse, not accepted silently."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["validate", "--checks", "bogus"])


def _make_two_file_stub(captured_kwargs):
    """Build a fake Orchestrate whose validate() records its kwargs, returning a two-file report."""
    finding = Finding(
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
        module_name="momma",
        component="stage",
        filepath="flpe/momma/{reach_id}_momma.nc",
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
        results_file="a_momma.nc",
    )
    other = Finding(
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
        module_name="momma",
        component="stage",
        filepath="flpe/momma/{reach_id}_momma.nc",
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
        results_file="b_momma.nc",
    )

    class _StubOrchestrate:
        def validate(self, data_mount, *, strict=False, modules=None, **kwargs):
            self.data_mount = data_mount
            captured_kwargs.update(kwargs)
            return Report([finding, other], **kwargs)

    return _StubOrchestrate


def test_max_files_implies_show_files(monkeypatch, capsys):
    """Passing --max-files alone (without --show-files) still enables the file lists."""
    captured_kwargs = {}
    monkeypatch.setattr("cit.__main__.Orchestrate", _make_two_file_stub(captured_kwargs))
    monkeypatch.setattr(
        "sys.argv", ["cit", "validate", "--results", "/mnt/data", "--max-files", "1"]
    )

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["show_files"] is True
    assert captured_kwargs["max_files"] == 1


def test_max_files_absent_resolves_to_default(monkeypatch, capsys):
    """With no --max-files/--show-files at all, the resolved kwargs are the documented default."""
    captured_kwargs = {}
    monkeypatch.setattr("cit.__main__.Orchestrate", _make_two_file_stub(captured_kwargs))
    monkeypatch.setattr("sys.argv", ["cit", "validate", "--results", "/mnt/data"])

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["show_files"] is False
    assert captured_kwargs["max_files"] == DEFAULT_MAX_FILES


def test_max_files_set_to_default_value_still_enables_show_files(monkeypatch, capsys):
    """--max-files 5 (the default value, passed explicitly) must still turn file lists on.

    This is the case a `!= DEFAULT_MAX_FILES` sentinel gets wrong: the value looks like the
    default, but the user explicitly asked for a cap and should get the file lists.
    """
    captured_kwargs = {}
    monkeypatch.setattr("cit.__main__.Orchestrate", _make_two_file_stub(captured_kwargs))
    monkeypatch.setattr(
        "sys.argv",
        ["cit", "validate", "--results", "/mnt/data", "--max-files", str(DEFAULT_MAX_FILES)],
    )

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["show_files"] is True
    assert captured_kwargs["max_files"] == DEFAULT_MAX_FILES


def test_checks_value_reaches_orchestrate_validate(monkeypatch):
    """--checks structure reaches Orchestrate.validate as ValidationSource.STRUCTURE, not a str."""
    captured_kwargs = {}
    monkeypatch.setattr("cit.__main__.Orchestrate", _make_two_file_stub(captured_kwargs))
    monkeypatch.setattr(
        "sys.argv", ["cit", "validate", "--results", "/mnt/data", "--checks", "structure"]
    )

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["checks"] is ValidationSource.STRUCTURE


def test_checks_all_reaches_orchestrate_validate_as_none(monkeypatch):
    """The default --checks all reaches Orchestrate.validate as None (render every section)."""
    captured_kwargs = {}
    monkeypatch.setattr("cit.__main__.Orchestrate", _make_two_file_stub(captured_kwargs))
    monkeypatch.setattr("sys.argv", ["cit", "validate", "--results", "/mnt/data"])

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["checks"] is None


def test_max_files_below_one_exits_with_clear_message(capsys):
    """--max-files 0 exits with a clear message rather than a traceback."""
    args = build_parser().parse_args(["validate", "--results", "/mnt/data", "--max-files", "0"])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)

    assert "--max-files" in str(excinfo.value)


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
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
    )
    report = Report([finding])

    class _StubOrchestrate:
        def validate(
            self,
            data_mount,
            *,
            strict=False,
            modules=None,
            show_passed=False,
            show_files=False,
            max_files=5,
            checks=None,
        ):
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
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
    )
    report = Report([finding])

    class _StubOrchestrate:
        def validate(
            self,
            data_mount,
            *,
            strict=False,
            modules=None,
            show_passed=False,
            show_files=False,
            max_files=5,
            checks=None,
        ):
            return report

    monkeypatch.setattr("cit.__main__.Orchestrate", _StubOrchestrate)
    monkeypatch.setattr("sys.argv", ["cit", "validate", "--results", "/mnt/data"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "extra_var" in captured.out
    assert captured.err == ""
