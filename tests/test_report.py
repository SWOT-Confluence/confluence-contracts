"""Tests for the ``Finding`` dataclass (cit.report).

Covers the P1-9.1 additions: ``results_file`` (the resolved produced file, defaulted so existing
callers keep working) and ``check`` (what kind of thing was examined, required so every finding
names it rather than silently defaulting to an empty string).
"""

import pytest

from cit.report import Finding, FindingStatus, FindingType

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
