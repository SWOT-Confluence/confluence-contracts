"""Tests for ``cit.report``: the ``Finding`` dataclass and the ``Report`` aggregator.

Covers the P1-9.1 additions to ``Finding`` -- ``results_file`` (the resolved produced file,
defaulted so existing callers keep working) and ``check`` (what kind of thing was examined,
required so every finding names it rather than silently defaulting to an empty string) -- the
P1-9.2 ``Report``: deduplication, grouping, and the exit-code policy -- and the P1-9.3
``Report.__str__`` rendering: banner, legend, counts line, and component-first grouping.
"""

import csv
from dataclasses import fields, replace
from pathlib import Path

import pytest

from cit.contract import Contract
from cit.report import Finding, FindingStatus, FindingType, Report

_BASE = {
    "type": FindingType.PASSED,
    "status": FindingStatus.INFO,
    "module_name": "momma",
    "component": "stage",
    "filepath": "flpe/momma/{reach_id}_momma.nc",
    "validation": "contract",
}


def test_results_file_defaults_to_empty_string():
    """results_file is optional, so a caller with no resolved file yet need not pass it."""
    finding = Finding(**_BASE, check="variable")

    assert finding.results_file == ""


def test_results_file_can_be_set():
    """results_file carries the resolved file path, distinct from the filepath template."""
    finding = Finding(**_BASE, check="variable", results_file="flpe/momma/74267700071_momma.nc")

    assert finding.results_file == "flpe/momma/74267700071_momma.nc"
    assert finding.results_file != finding.filepath


def test_check_is_required():
    """The check field has no default: every finding must name what kind of thing was examined."""
    with pytest.raises(TypeError):
        Finding(**_BASE)


def test_finding_is_still_hashable():
    """Finding stays hashable with the new fields, so a report may dedupe with a Counter."""
    finding = Finding(**_BASE, check="variable")

    assert hash(finding) == hash(finding)
    assert len({finding, finding}) == 1


def _finding(**overrides: object) -> Finding:
    """Build a Finding from ``_BASE`` with ``check="variable"``, overridable per call."""
    kwargs = {**_BASE, "check": "variable", **overrides}
    return Finding(**kwargs)


def test_grouped_by_orders_fail_before_warn_before_pass():
    """Within a group, findings sort FAIL -> WARN -> PASS (carried as status INFO)."""
    passed = _finding(status=FindingStatus.INFO, component="stage")
    warn = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN, component="extra_var")
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL, component="Qout")

    report = Report([passed, warn, fail])

    groups = report.grouped_by(lambda finding: finding.module_name)

    assert [finding.status for finding in groups["momma"]] == [
        FindingStatus.FAIL,
        FindingStatus.WARN,
        FindingStatus.INFO,
    ]


def test_dedup_collapses_findings_differing_only_by_results_file():
    """The same finding recurring across files (once per reach) collapses to one entry."""
    seen_in_a = _finding(results_file="a_momma.nc")
    seen_in_b = replace(seen_in_a, results_file="b_momma.nc")

    (entry,) = Report([seen_in_a, seen_in_b]).deduplicated()

    assert entry.count == 2
    assert entry.files == ("a_momma.nc", "b_momma.nc")
    assert entry.finding.results_file == ""


def test_dedup_keeps_separate_entries_for_different_messages():
    """A differing message is a genuinely different finding, not a duplicate."""
    expected_f4 = _finding(message="expected f4, got f8")
    expected_i4 = replace(expected_f4, message="expected i4, got i8")

    entries = Report([expected_f4, expected_i4]).deduplicated()

    assert len(entries) == 2


def test_dedup_keeps_separate_entries_for_same_component_different_check():
    """A dimension and a variable can share a component name (e.g. 'nt') but are not duplicates."""
    as_dimension = _finding(component="nt", check="dimension")
    as_variable = replace(as_dimension, check="variable")

    entries = Report([as_dimension, as_variable]).deduplicated()

    assert len(entries) == 2


def test_exit_code_is_1_with_any_fail():
    """A single FAIL is enough to fail the run, regardless of any other findings."""
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL)
    warn = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)

    assert Report([fail, warn]).exit_code == 1


def test_exit_code_is_0_for_warn_only():
    """A run with only WARNs (and PASSes) still exits 0."""
    warn = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)
    passed = _finding(status=FindingStatus.INFO)

    assert Report([warn, passed]).exit_code == 0


def test_exit_code_is_0_for_empty_run():
    """No findings at all is a passing run."""
    assert Report([]).exit_code == 0


def test_exit_code_is_0_when_only_finding_is_report_status():
    """REPORT findings never affect the exit code, even when the run is otherwise strict.

    Report itself takes no ``strict`` flag: any ``--strict`` promotion of rule WARNs already
    happened at emit time (see ``validation._status``), and REPORT is not part of that ladder at
    all, so there is nothing here for Report to re-escalate.
    """
    report_only = _finding(status=FindingStatus.REPORT)

    assert Report([report_only]).exit_code == 0


def test_grouped_by_is_deterministic():
    """Group key order and each group's finding order do not depend on insertion order."""
    findings = [
        _finding(module_name="sad", status=FindingStatus.WARN, type=FindingType.EXTRA),
        _finding(module_name="momma", status=FindingStatus.FAIL, type=FindingType.DIFFERENT),
        _finding(module_name="hivdi", status=FindingStatus.INFO),
    ]

    first = Report(findings).grouped_by(lambda finding: finding.module_name)
    second = Report(list(reversed(findings))).grouped_by(lambda finding: finding.module_name)

    assert list(first.keys()) == ["hivdi", "momma", "sad"]
    assert first == second


def test_report_findings_returns_every_raw_occurrence():
    """The raw findings stay reachable (not just the deduplicated view) for a full export."""
    seen_in_a = _finding(results_file="a_momma.nc")
    seen_in_b = replace(seen_in_a, results_file="b_momma.nc")

    report = Report([seen_in_a, seen_in_b])

    assert report.findings == [seen_in_a, seen_in_b]


# --- P1-9.3: Report.__str__ rendering -------------------------------------------------------


def test_str_includes_legend_and_counts_line():
    """print(report) shows the legend (both nouns) and a load-bearing counts line."""
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL, component="Qout")
    warn = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN, component="extra_var")
    passed = _finding(status=FindingStatus.INFO, component="Qout")

    text = str(Report([fail, warn, passed]))

    assert "Declared = what the contract" in text
    assert "Found    = what the produced file actually holds." in text
    assert "MISSING" in text and "EXTRA" in text and "DIFFERENT" in text and "PASSED" in text
    assert "FAIL 1" in text
    assert "WARN 1" in text
    assert "PASS 1" in text


def test_str_banner_degrades_without_contracts():
    """With no contracts supplied, the banner still renders -- just with no version segment."""
    text = str(Report([_finding()]))

    assert text.splitlines()[0].startswith("cit ")


def test_str_banner_shows_module_version_branch_commit(valid_contract):
    """The banner names each supplied contract's version, branch, and commit."""
    contract = Contract.model_validate(valid_contract)

    text = str(Report([_finding()], {"momma": contract}))

    banner = text.splitlines()[0]
    assert "momma" in banner
    assert contract.version in banner
    assert contract.source.branch in banner
    assert contract.source.commit in banner


def test_str_groups_module_then_filepath_then_component():
    """Findings render nested: module heading, then its filepath, then its components."""
    momma = _finding(module_name="momma", filepath="flpe/momma/{reach_id}_momma.nc")
    output = _finding(
        module_name="output",
        filepath="output/sos/{continent_id}_SOS_results.nc",
        status=FindingStatus.FAIL,
        type=FindingType.DIFFERENT,
    )

    text = str(Report([momma, output], show_passed=True))
    lines = text.splitlines()

    momma_idx = lines.index("momma")
    output_idx = lines.index("output")
    assert lines[momma_idx + 1].strip() == "flpe/momma/{reach_id}_momma.nc"
    assert lines[output_idx + 1].strip() == "output/sos/{continent_id}_SOS_results.nc"


def test_nt_case_shows_passed_and_non_passed_lines_for_one_component():
    """A component with mixed PASSED/non-PASSED findings shows both lines together.

    This is the momma 'nt' case: a PASSED dimension finding and a non-PASSED variable finding
    share a component name, and both render by default -- no --show-passed needed.
    """
    as_dimension = _finding(component="nt", check="dimension")
    as_variable = _finding(
        component="nt", check="variable", type=FindingType.EXTRA, status=FindingStatus.WARN
    )

    text = str(Report([as_dimension, as_variable]))

    nt_idx = text.splitlines().index("    nt")
    component_lines = text.splitlines()[nt_idx + 1 : nt_idx + 3]
    assert any("dimension" in line and "PASSED" in line for line in component_lines)
    assert any("variable" in line and "EXTRA" in line for line in component_lines)


def test_all_passed_component_hidden_by_default():
    """A component whose only finding is PASSED does not appear unless show_passed is set."""
    passed = _finding(component="stage")

    default_text = str(Report([passed]))
    shown_text = str(Report([passed], show_passed=True))

    assert "stage" not in default_text
    assert "stage" in shown_text


def test_components_sorted_by_worst_severity_then_name():
    """A FAIL-bearing component precedes a WARN-only component, alphabetical name breaks ties."""
    warn_component = _finding(
        component="zzz_extra", type=FindingType.EXTRA, status=FindingStatus.WARN
    )
    fail_component = _finding(
        component="Qout", type=FindingType.DIFFERENT, status=FindingStatus.FAIL
    )

    text = str(Report([warn_component, fail_component]))
    lines = text.splitlines()

    assert lines.index("    Qout") < lines.index("    zzz_extra")


def test_finding_with_empty_filepath_and_no_results_file_does_not_raise():
    """A registry-lint finding with nothing on disk (P1-17) renders without raising."""
    orphan = _finding(
        filepath="",
        results_file="",
        component="orphan_var",
        type=FindingType.MISSING,
        status=FindingStatus.FAIL,
    )

    text = str(Report([orphan]))

    assert "orphan_var" in text


def test_str_is_deterministic_across_runs():
    """Rendering the same findings twice (in different orders) produces identical text."""
    findings = [
        _finding(module_name="sad", status=FindingStatus.WARN, type=FindingType.EXTRA),
        _finding(module_name="momma", status=FindingStatus.FAIL, type=FindingType.DIFFERENT),
        _finding(module_name="hivdi", status=FindingStatus.INFO),
    ]

    first = str(Report(findings))
    second = str(Report(list(reversed(findings))))

    assert first == second


def test_show_passed_does_not_affect_exit_code():
    """show_passed only changes what __str__ renders, never the pass/fail policy."""
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL)

    assert Report([fail], show_passed=True).exit_code == Report([fail]).exit_code == 1


def test_write_csv_has_one_row_per_finding_no_deduplication(tmp_path: Path):
    """write_csv writes every occurrence, unlike the deduplicated text report."""
    findings = [
        _finding(results_file="flpe/momma/1_momma.nc"),
        _finding(results_file="flpe/momma/2_momma.nc"),
        _finding(results_file="flpe/momma/3_momma.nc"),
    ]
    csv_path = tmp_path / "report.csv"

    Report(findings).write_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert len(rows) == 3
    assert {row["results_file"] for row in rows} == {
        "flpe/momma/1_momma.nc",
        "flpe/momma/2_momma.nc",
        "flpe/momma/3_momma.nc",
    }


def test_write_csv_header_matches_finding_dataclass_fields(tmp_path: Path):
    """The CSV header is derived from Finding's dataclass fields, in declared order."""
    csv_path = tmp_path / "report.csv"

    Report([_finding()]).write_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        header = next(csv.reader(csv_file))

    assert header == [f.name for f in fields(Finding)]


def test_write_csv_accepts_str_path(tmp_path: Path):
    """write_csv accepts a plain string path, not just a Path object."""
    csv_path = tmp_path / "report.csv"

    Report([_finding()]).write_csv(str(csv_path))

    assert csv_path.exists()


def test_write_csv_serializes_enum_fields_as_plain_strings(tmp_path: Path):
    """type/status land as plain StrEnum values (e.g. 'MISSING'), not 'FindingType.MISSING'."""
    csv_path = tmp_path / "report.csv"
    finding = _finding(type=FindingType.MISSING, status=FindingStatus.FAIL)

    Report([finding]).write_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        row = next(csv.DictReader(csv_file))

    assert row["type"] == "MISSING"
    assert row["status"] == "FAIL"


def test_write_csv_empty_report_writes_header_only(tmp_path: Path):
    """An empty findings list still writes a valid CSV with just the header row."""
    csv_path = tmp_path / "report.csv"

    Report([]).write_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.reader(csv_file))

    assert rows == [[f.name for f in fields(Finding)]]


def test_write_csv_row_order_is_deterministic(tmp_path: Path):
    """Rows are ordered by the same sort key the text report uses, for a meaningful diff."""
    findings = [
        _finding(module_name="sad", status=FindingStatus.WARN, type=FindingType.EXTRA),
        _finding(module_name="momma", status=FindingStatus.FAIL, type=FindingType.DIFFERENT),
        _finding(module_name="hivdi", status=FindingStatus.INFO),
    ]
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"

    Report(findings).write_csv(first_path)
    Report(list(reversed(findings))).write_csv(second_path)

    assert first_path.read_text() == second_path.read_text()
