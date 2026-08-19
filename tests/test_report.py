"""Tests for ``cit.report``: the ``Finding`` dataclass and the ``Report`` aggregator.

Covers the P1-9.1 additions to ``Finding`` -- ``results_file`` (the resolved produced file,
defaulted so existing callers keep working) and ``check`` (what kind of thing was examined,
required so every finding names it rather than silently defaulting to an empty string) -- and the
P1-9.2 ``Report``: deduplication, grouping, and the exit-code policy.
"""

from dataclasses import replace

import pytest

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
