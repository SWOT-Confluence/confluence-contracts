"""End-to-end ``cit validate`` tests against a synthetic, fully-controlled fixture.

No bundled contract on the real mount can demonstrate every exit-code acceptance criterion
cleanly (see confluence-contracts-pj2.6): ``momma.yml`` only declares two of the 39 variables
its files actually hold (WARN-only, already demonstrable for real), and ``output.yml`` already
carries structural FAILs on its own, so ``--strict`` has no WARN-only baseline to flip off of.
These tests build a tiny NetCDF file (via ``netCDF4``, written to ``tmp_path``) plus a matching
contract and, for the ``--strict`` case, a matching rules artifact -- both discovered the same
way :mod:`tests.test_orchestrate` does, by monkeypatching ``cit.orchestrate.find_contract_files``
and ``find_rules_files`` rather than adding a ``--contracts``/``--rules`` CLI flag (deliberately
dropped from the CLI surface). ``cit.__main__.main`` itself is exercised unmodified, so this is a
real, if hermetic, run of the CLI end to end -- argument parsing, ``Orchestrate``, both
validators, and the exit policy.

Covers:

- a fully contract-matching file with no rules artifact -> an all-PASS run, exit 0;
- the same fixture with one variable's dtype changed -> a FAIL is introduced, exit 1;
- a rules artifact requiring a global attribute the file omits -> WARN-only, exit 0, and the
  identical run under ``--strict`` -> exit 1 (the flip real-mount data cannot demonstrate);
- ``--show-passed`` revealing the all-PASSED components the default run hides;
- ``--report`` writing the same text that went to stdout, and ``--csv`` writing a readable file.
"""

import csv
import sys
from io import StringIO
from pathlib import Path

import netCDF4 as nc
import pytest

from cit.__main__ import main

MODULE = "synth"
FILEPATH = "synth/{reach_id}_synth.nc"

# A contract with one produced file, one dimension, and one required f8 variable -- the smallest
# fixture that still exercises the structural validator's dimension and variable checks.
CONTRACT_YAML = f"""\
version: "1.0.0"
source:
  repo: synth
  github_username: octocat
  branch: main
  commit: 0123456789abcdef0123456789abcdef01234567
  image_tag: synth:latest
module:
  name: {MODULE}
  produces:
    - filepath: {FILEPATH}
      dimensions: [nt]
      variables:
        stage:
          dtype: f8
          dimensions: [nt]
          required: true
  consumes: []
"""

# Requires one global attribute ("title") the fixture file below never sets, and models no
# variable attributes at all -- so the only *escalatable* finding is the missing global
# attribute, isolating the --strict WARN -> FAIL flip from any other noise.
RULES_YAML = f"""\
module_name: {MODULE}
filepath: {FILEPATH}
global_attributes: [title]
variable_attributes: {{}}
fill_values: {{}}
"""


def _write_nc(path: Path, dtype: str = "f8") -> None:
    """Write a minimal NetCDF file: one dimension ``nt`` (size 3), one variable ``stage``.

    Args:
        path: Where to write the ``.nc`` file.
        dtype: The netCDF4 dtype token to create ``stage`` with (default matches the contract's
            declared ``f8``; a caller passes something else to introduce a FAIL).
    """
    ds = nc.Dataset(path, "w")
    ds.createDimension("nt", 3)
    ds.createVariable("stage", dtype, ("nt",))
    ds.close()


@pytest.fixture
def contract_file(tmp_path):
    """Write ``CONTRACT_YAML`` to a temp file and return its path."""
    path = tmp_path / "synth.yml"
    path.write_text(CONTRACT_YAML)
    return path


@pytest.fixture
def rules_file(tmp_path):
    """Write ``RULES_YAML`` to a temp file and return its path."""
    path = tmp_path / "synth_rules.yml"
    path.write_text(RULES_YAML)
    return path


@pytest.fixture
def mount(tmp_path):
    """A run mount holding one synth result file matching ``CONTRACT_YAML`` exactly."""
    result_dir = tmp_path / "mnt" / "synth"
    result_dir.mkdir(parents=True)
    _write_nc(result_dir / "11111111111_synth.nc")
    return tmp_path / "mnt"


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    contract_file: Path,
    mount: Path,
    *,
    extra_args: list[str] | None = None,
    rules_files: list[Path] | None = None,
) -> int:
    """Run ``cit validate`` end to end against the synthetic fixture.

    Points the orchestrator's contract/rules discovery at the temp files built by the fixtures
    above (rather than the bundled package data), then drives the real CLI entry point exactly
    as a user would invoke it.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        contract_file: Path to a temp contract YAML (from the ``contract_file`` fixture).
        mount: The run mount root to validate (from the ``mount`` fixture).
        extra_args: Additional ``cit validate`` CLI flags, e.g. ``["--strict"]``.
        rules_files: Temp rules YAML paths to use in place of the bundled rules artifact;
            defaults to none, so no ``RulesValidator`` finding is produced.

    Returns:
        The process exit code ``main()`` would have raised via ``SystemExit``.
    """
    monkeypatch.setattr("cit.orchestrate.find_contract_files", lambda: [contract_file])
    monkeypatch.setattr("cit.orchestrate.find_rules_files", lambda: rules_files or [])
    monkeypatch.setattr(
        "sys.argv", ["cit", "validate", "--results", str(mount), *(extra_args or [])]
    )
    with pytest.raises(SystemExit) as excinfo:
        main()
    return excinfo.value.code


def test_clean_run_is_all_pass_and_exits_zero(contract_file, mount, monkeypatch, capsys):
    """A contract-matching file with no rules artifact yields an all-PASS run and exits 0.

    No contract on the real mount produces this (see confluence-contracts-pj2.6): this is the
    genuinely clean baseline the epic's exit-code policy assumes exists.
    """
    exit_code = _run_cli(monkeypatch, contract_file, mount)

    assert exit_code == 0
    text = capsys.readouterr().out
    assert "FAIL 0" in text
    assert "WARN 0" in text


def test_fail_introduced_exits_one(contract_file, tmp_path, monkeypatch, capsys):
    """Changing the file's dtype away from the contract's declared f8 introduces a FAIL."""
    result_dir = tmp_path / "mnt2" / "synth"
    result_dir.mkdir(parents=True)
    _write_nc(result_dir / "11111111111_synth.nc", dtype="i4")

    exit_code = _run_cli(monkeypatch, contract_file, tmp_path / "mnt2")

    assert exit_code == 1
    text = capsys.readouterr().out
    assert "FAIL 1" in text
    assert "f8" in text and "i4" in text


def test_fail_finding_names_the_lone_result_file_basename(
    contract_file, tmp_path, monkeypatch, capsys
):
    """A finding seen in exactly one file names that file's basename, not 'x1 file'."""
    result_dir = tmp_path / "mnt3" / "synth"
    result_dir.mkdir(parents=True)
    _write_nc(result_dir / "11111111111_synth.nc", dtype="i4")

    exit_code = _run_cli(monkeypatch, contract_file, tmp_path / "mnt3")

    assert exit_code == 1
    text = capsys.readouterr().out
    assert "11111111111_synth.nc" in text
    assert "x1 file" not in text


def test_strict_flips_rule_warning_to_fail_exit_code(
    contract_file, mount, rules_file, monkeypatch, capsys
):
    """A rule-WARN-only run exits 0; the identical run under --strict exits 1.

    This is the one criterion real-mount data cannot demonstrate: the only module with rule
    findings on the real mount (``output``) already exits 1 on structural FAILs alone, so there
    is no WARN -> FAIL flip to observe there. This fixture isolates it.
    """
    warn_only_exit = _run_cli(monkeypatch, contract_file, mount, rules_files=[rules_file])
    assert warn_only_exit == 0
    warn_text = capsys.readouterr().out
    assert "FAIL 0" in warn_text
    assert "title" in warn_text  # the missing global attribute is visible even though it warns

    strict_exit = _run_cli(
        monkeypatch, contract_file, mount, extra_args=["--strict"], rules_files=[rules_file]
    )
    assert strict_exit == 1
    strict_text = capsys.readouterr().out
    assert "FAIL 0" not in strict_text
    assert "title" in strict_text


def test_structure_and_metadata_findings_reach_stdout_distinguishably(
    contract_file, rules_file, tmp_path, monkeypatch, capsys
):
    """A structure finding and a metadata finding for the same run render as distinct lines.

    Before #10 a contract-side and a rule-side finding with the same shape (e.g. two
    ``variable MISSING`` lines, one FAIL, one WARN) rendered byte-identical; this drives the
    real CLI end to end and checks the two sources are visibly labelled.
    """
    result_dir = tmp_path / "mnt4" / "synth"
    result_dir.mkdir(parents=True)
    ds = nc.Dataset(result_dir / "11111111111_synth.nc", "w")
    ds.createDimension("nt", 3)
    ds.createVariable("stage", "f8", ("nt",))
    ds.createVariable("extra_var", "f8", ("nt",))  # undeclared -> structure EXTRA/WARN
    ds.close()

    exit_code = _run_cli(
        monkeypatch, contract_file, tmp_path / "mnt4", rules_files=[rules_file]
    )

    assert exit_code == 0  # both findings are WARN-only
    text = capsys.readouterr().out
    finding_lines = [line for line in text.splitlines() if "WARN" in line]
    assert any("structure" in line for line in finding_lines)
    assert any("metadata" in line for line in finding_lines)


def test_show_passed_reveals_all_passed_components(contract_file, mount, monkeypatch, capsys):
    """--show-passed renders the all-PASSED components the default run hides."""
    _run_cli(monkeypatch, contract_file, mount)
    default_text = capsys.readouterr().out

    _run_cli(monkeypatch, contract_file, mount, extra_args=["--show-passed"])
    shown_text = capsys.readouterr().out

    assert "stage" not in default_text
    assert "stage" in shown_text


def test_report_flag_writes_the_same_text_as_stdout(contract_file, mount, tmp_path, monkeypatch):
    """--report PATH writes byte-identical text to what was printed to stdout."""
    report_path = tmp_path / "report.txt"

    monkeypatch.setattr("cit.orchestrate.find_contract_files", lambda: [contract_file])
    monkeypatch.setattr("cit.orchestrate.find_rules_files", lambda: [])
    monkeypatch.setattr(
        "sys.argv", ["cit", "validate", "--results", str(mount), "--report", str(report_path)]
    )

    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(SystemExit):
        main()

    assert report_path.read_text() == stdout.getvalue()


def test_csv_flag_writes_a_readable_file(contract_file, mount, tmp_path, monkeypatch, capsys):
    """--csv PATH writes a CSV a reader can parse, with a header and one row per finding."""
    csv_path = tmp_path / "findings.csv"

    _run_cli(monkeypatch, contract_file, mount, extra_args=["--csv", str(csv_path)])
    capsys.readouterr()  # drain stdout; not under test here

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows, "expected at least one finding row"
    assert set(rows[0]) == {
        "type",
        "status",
        "module_name",
        "component",
        "filepath",
        "validation",
        "message",
        "results_file",
        "scope",
        "check",
    }
    assert all(row["module_name"] == MODULE for row in rows)
