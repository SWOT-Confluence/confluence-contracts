"""Tests for ``cit.report``: the ``Finding`` dataclass and the ``Report`` aggregator.

Covers the P1-9.1 additions to ``Finding`` -- ``results_file`` (the resolved produced file,
defaulted so existing callers keep working) and ``scope`` (what kind of thing was examined,
required so every finding names it rather than silently defaulting to an empty string) -- the
P1-9.2 ``Report``: deduplication, grouping, and the exit-code policy -- the P1-9.3
``Report.__str__`` rendering: banner, legend, counts line, and component-first grouping -- and
the #10 six-column grid: ``scope`` renamed from ``check``, a new ``check`` label field, the
``structure``/``metadata`` source rename, and the global-attribute block.
"""

import csv
from dataclasses import fields, replace
from pathlib import Path

import pytest

from cit import report
from cit.contract import Contract
from cit.report import Check, Finding, FindingStatus, FindingType, Report, ValidationSource

_BASE = {
    "type": FindingType.PASSED,
    "status": FindingStatus.INFO,
    "module_name": "momma",
    "component": "stage",
    "filepath": "flpe/momma/{reach_id}_momma.nc",
    "validation": ValidationSource.STRUCTURE,
}


def test_results_file_defaults_to_empty_string():
    """results_file is optional, so a caller with no resolved file yet need not pass it."""
    finding = Finding(**_BASE, scope="variable", check=Check.EXISTS)

    assert finding.results_file == ""


def test_results_file_can_be_set():
    """results_file carries the resolved file path, distinct from the filepath template."""
    finding = Finding(
        **_BASE,
        scope="variable",
        check=Check.EXISTS,
        results_file="flpe/momma/74267700071_momma.nc",
    )

    assert finding.results_file == "flpe/momma/74267700071_momma.nc"
    assert finding.results_file != finding.filepath


def test_scope_is_required():
    """The scope field has no default: every finding must name what kind of thing was examined."""
    with pytest.raises(TypeError):
        Finding(**_BASE, check=Check.EXISTS)


def test_check_is_required():
    """The check field has no default: every finding must name what question was asked."""
    with pytest.raises(TypeError):
        Finding(**_BASE, scope="variable")


def test_finding_is_still_hashable():
    """Finding stays hashable with the new fields, so a report may dedupe with a Counter."""
    finding = Finding(**_BASE, scope="variable", check=Check.EXISTS)

    assert hash(finding) == hash(finding)
    assert len({finding, finding}) == 1


def _finding(**overrides: object) -> Finding:
    """Build a Finding from ``_BASE`` with ``scope="variable"``/``check=EXISTS``, overridable."""
    kwargs = {**_BASE, "scope": "variable", "check": Check.EXISTS, **overrides}
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

    (entry,) = Report([seen_in_a, seen_in_b]).deduplicated

    assert entry.count == 2
    assert entry.files == ("a_momma.nc", "b_momma.nc")
    assert entry.finding.results_file == ""


def test_dedup_keeps_separate_entries_for_different_messages():
    """A differing message is a genuinely different finding, not a duplicate."""
    expected_f4 = _finding(message="expected f4, got f8")
    expected_i4 = replace(expected_f4, message="expected i4, got i8")

    entries = Report([expected_f4, expected_i4]).deduplicated

    assert len(entries) == 2


def test_dedup_keeps_separate_entries_for_same_component_different_scope():
    """A dimension and a variable can share a component name (e.g. 'nt') but are not duplicates."""
    as_dimension = _finding(component="nt", scope="dimension")
    as_variable = replace(as_dimension, scope="variable")

    entries = Report([as_dimension, as_variable]).deduplicated

    assert len(entries) == 2


def test_deduplicated_is_cached_after_first_call():
    """Deduplicated computes once per Report instance; the second access reuses the cache.

    ``cached_property`` stores its computed value in the instance ``__dict__`` under the
    property's own name, so checking for that key is a reliable way to detect that the
    computation ran (and ran exactly once) without reaching into private call-counting.
    """
    report = Report([_finding()])

    assert "deduplicated" not in report.__dict__

    first = report.deduplicated

    assert "deduplicated" in report.__dict__

    second = report.deduplicated

    assert first == second


def test_deduplicated_returns_an_immutable_sequence():
    """Deduplicated is a tuple of frozen entries, so a caller cannot corrupt the cached value."""
    report = Report([_finding()])

    deduped = report.deduplicated

    assert isinstance(deduped, tuple)
    with pytest.raises(AttributeError):
        deduped.clear()
    with pytest.raises(TypeError):
        deduped[0] = deduped[0]


def test_init_copies_findings_list():
    """__init__ copies its argument, so mutating the caller's list after construction is inert."""
    findings = [_finding()]
    report = Report(findings)

    findings.append(_finding(component="other"))

    assert len(report.findings) == 1
    assert len(report.deduplicated) == 1


def test_deduplicated_count_equals_len_files():
    """Count is the raw occurrence count, which always equals len(files) (no duplicate files)."""
    seen_in_a = _finding(results_file="a_momma.nc")
    seen_in_b = replace(seen_in_a, results_file="b_momma.nc")
    seen_in_c = replace(seen_in_a, results_file="c_momma.nc")

    (entry,) = Report([seen_in_a, seen_in_b, seen_in_c]).deduplicated

    assert entry.count == len(entry.files) == 3


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
    as_dimension = _finding(component="nt", scope="dimension")
    as_variable = _finding(
        component="nt", scope="variable", type=FindingType.EXTRA, status=FindingStatus.WARN
    )

    text = str(Report([as_dimension, as_variable]))

    nt_idx = text.splitlines().index(f"{report._INDENT * 2}nt")
    # nt_idx + 1 is the header row; the finding lines follow it.
    component_lines = text.splitlines()[nt_idx + 2 : nt_idx + 4]
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

    assert lines.index(f"{report._INDENT * 2}Qout") < lines.index(
        f"{report._INDENT * 2}zzz_extra"
    )


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
    shown_text = str(Report([orphan], show_files=True))

    assert "orphan_var" in text
    assert "orphan_var" in shown_text
    # No file behind the finding at all: no suffix and no list, even with show_files=True.
    # component_idx + 1 is the header row (which itself says "files"); the finding row follows.
    component_idx = text.splitlines().index(f"{report._INDENT * 2}orphan_var")
    assert "file" not in text.splitlines()[component_idx + 2]


# --- file-name rendering (x1 file -> basename, --show-files, --max-files) -------------------


def test_lone_file_renders_basename_not_x1_file():
    """A finding seen in exactly one file names that file, not the uninformative 'x1 file'."""
    finding = _finding(
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        results_file="flpe/momma/74267700071_momma.nc",
    )

    text = str(Report([finding]))

    assert "74267700071_momma.nc" in text
    assert "x1 file" not in text


def test_two_files_render_count_and_no_list_by_default():
    """A finding seen in two files still renders just the count, with no list, by default."""
    seen_in_a = _finding(
        type=FindingType.EXTRA, status=FindingStatus.WARN, results_file="a_momma.nc"
    )
    seen_in_b = replace(seen_in_a, results_file="b_momma.nc")

    text = str(Report([seen_in_a, seen_in_b]))

    assert "x2 files" in text
    assert "a_momma.nc" not in text
    assert "b_momma.nc" not in text


def test_show_files_lists_both_basenames_one_per_line():
    """show_files=True lists each basename beneath the finding, at the continuation indent."""
    seen_in_a = _finding(
        type=FindingType.EXTRA, status=FindingStatus.WARN, results_file="a_momma.nc"
    )
    seen_in_b = replace(seen_in_a, results_file="b_momma.nc")

    text = str(Report([seen_in_a, seen_in_b], show_files=True))
    lines = text.splitlines()

    expected_prefix = f"{report._INDENT * 3}{report._grid_continuation(report._GRID)}"
    assert f"{expected_prefix}a_momma.nc" in lines
    assert f"{expected_prefix}b_momma.nc" in lines


def test_show_files_truncates_at_max_files_with_overflow_line():
    """With seven files and max_files=5, exactly five are listed plus one overflow line."""
    base = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(7)]

    text = str(Report(findings, show_files=True, max_files=5))
    lines = text.splitlines()

    basenames_shown = [f"{i}_momma.nc" for i in range(5)]
    for basename in basenames_shown:
        assert any(basename in line for line in lines)
    assert not any("5_momma.nc" in line for line in lines)
    assert not any("6_momma.nc" in line for line in lines)
    assert any("... and 2 more (use --csv for the full list)" in line for line in lines)


def test_show_files_no_overflow_line_when_not_truncated():
    """When the file count is at or below max_files, no overflow line is rendered."""
    base = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(3)]

    text = str(Report(findings, show_files=True, max_files=5))

    assert "more" not in text


def test_show_files_single_file_named_inline_not_repeated_as_list():
    """A single file under show_files=True is named inline and not repeated as a list line."""
    finding = _finding(
        type=FindingType.EXTRA, status=FindingStatus.WARN, results_file="74267700071_momma.nc"
    )

    text = str(Report([finding], show_files=True))

    assert text.count("74267700071_momma.nc") == 1


def test_show_files_does_not_affect_exit_code():
    """show_files only changes what __str__ renders, never the pass/fail policy."""
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL)

    assert Report([fail], show_files=True).exit_code == Report([fail]).exit_code == 1


def test_str_is_deterministic_across_runs_with_show_files():
    """Rendering with show_files=True is also deterministic across differently-ordered input."""
    base = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(7)]

    first = str(Report(findings, show_files=True, max_files=5))
    second = str(Report(list(reversed(findings)), show_files=True, max_files=5))

    assert first == second


# --- dangling module/filepath headings (all-PASS sections render nothing) -------------------


def test_all_passed_module_hides_module_and_filepath_headings_by_default():
    """A module whose components are all PASSED emits neither its module nor filepath heading."""
    passed = _finding(module_name="momma", filepath="flpe/momma/{reach_id}_momma.nc")

    default_text = str(Report([passed]))
    shown_text = str(Report([passed], show_passed=True))

    assert "momma" not in default_text
    assert "flpe/momma/{reach_id}_momma.nc" not in default_text
    assert "momma" in shown_text
    assert "flpe/momma/{reach_id}_momma.nc" in shown_text


def test_all_pass_body_message_distinct_from_empty_report_message():
    """An all-PASS run says so explicitly, rather than reusing the empty-report '(no findings)'."""
    passed = _finding()

    all_pass_text = str(Report([passed]))
    empty_text = str(Report([]))

    assert "(no findings)" in empty_text
    assert "(no findings)" not in all_pass_text
    assert "every component passed" in all_pass_text


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


# --- #10: check/scope columns, structure-before-metadata, global attributes, SKIPPED --------


@pytest.mark.parametrize("check", list(Check))
def test_every_check_label_renders_in_a_component_row(check):
    """Each of the eight check labels renders in its own component's check-column row."""
    finding = _finding(
        scope="variable", check=check, type=FindingType.EXTRA, status=FindingStatus.WARN
    )

    body = Report([finding])._render_findings()

    assert check in body


def test_write_csv_includes_every_scope_value(tmp_path: Path):
    """All four scopes -- including global_attribute -- round-trip through the CSV export."""
    findings = [
        _finding(scope="dimension", check=Check.EXISTS, component="nt"),
        _finding(scope="variable", check=Check.DTYPE, component="stage"),
        _finding(scope="attribute", check=Check.BOUNDS, component="stage"),
        _finding(scope="global_attribute", check=Check.EXISTS, component="title"),
    ]
    csv_path = tmp_path / "report.csv"

    Report(findings).write_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert {row["scope"] for row in rows} == {
        "dimension",
        "variable",
        "attribute",
        "global_attribute",
    }


def test_structure_precedes_metadata_within_a_component_regardless_of_severity():
    """Structure sorts before metadata within a component even when metadata is more severe."""
    metadata_fail = _finding(
        validation=ValidationSource.METADATA,
        scope="attribute",
        check=Check.BOUNDS,
        type=FindingType.DIFFERENT,
        status=FindingStatus.FAIL,
        component="stage",
    )
    structure_warn = _finding(
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        component="stage",
    )

    body = Report([metadata_fail, structure_warn])._render_findings()

    structure_idx = body.index("structure")
    metadata_idx = body.index("metadata")
    assert structure_idx < metadata_idx


def test_header_row_appears_once_per_shown_component():
    """Each shown component prints its own header row, once, at the finding indent."""
    finding_a = _finding(component="a", type=FindingType.EXTRA, status=FindingStatus.WARN)
    finding_b = _finding(component="b", type=FindingType.EXTRA, status=FindingStatus.WARN)

    body = Report([finding_a, finding_b])._render_findings()

    header = f"{report._INDENT * 3}{report._grid_header(report._GRID)}"
    assert body.count(header) == 2


def test_fail_and_warn_missing_findings_no_longer_render_identically():
    """A structure MISSING/FAIL and a metadata MISSING/WARN for the same variable now differ.

    Before #10 both rendered as the byte-identical ``variable  MISSING  x... files``.
    """
    structure_missing = _finding(
        validation=ValidationSource.STRUCTURE,
        scope="variable",
        check=Check.EXISTS,
        type=FindingType.MISSING,
        status=FindingStatus.FAIL,
        component="observations",
    )
    metadata_missing = _finding(
        validation=ValidationSource.METADATA,
        scope="variable",
        check=Check.EXISTS,
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
        component="observations",
    )

    body = Report([structure_missing, metadata_missing])._render_findings()
    lines = [line for line in body.splitlines() if "MISSING" in line]

    assert len(lines) == 2
    assert lines[0] != lines[1]


def test_global_attribute_block_renders_above_components_with_its_own_header():
    """The global-attribute block appears before any component, with its own header row."""
    global_attribute = _finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )
    component_finding = _finding(
        scope="variable",
        check=Check.EXISTS,
        component="stage",
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
    )

    body = Report([global_attribute, component_finding])._render_findings()
    lines = body.splitlines()

    global_heading_idx = lines.index(f"{report._INDENT * 2}global attributes")
    stage_heading_idx = lines.index(f"{report._INDENT * 2}stage")
    assert global_heading_idx < stage_heading_idx
    assert "attribute" in lines[global_heading_idx + 1]
    assert "title" in lines[global_heading_idx + 2]


def test_global_attribute_block_omitted_when_there_are_no_global_attribute_findings():
    """No global-attribute findings means no 'global attributes' heading at all."""
    component_finding = _finding(
        scope="variable",
        check=Check.EXISTS,
        component="stage",
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
    )

    body = Report([component_finding])._render_findings()

    assert "global attributes" not in body


def test_global_attribute_findings_do_not_render_as_a_component():
    """A global attribute's component name never appears as a component heading."""
    global_attribute = _finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )

    body = Report([global_attribute])._render_findings()

    assert f"{report._INDENT * 2}title" not in body.splitlines()


def test_skipped_report_finding_renders_and_keeps_its_component_visible():
    """A SKIPPED/REPORT finding is not PASSED, so its component shows without show_passed."""
    skipped = _finding(type=FindingType.SKIPPED, status=FindingStatus.REPORT, component="flag")

    text = str(Report([skipped]))

    assert "flag" in text
    assert "SKIPPED" in text
    assert Report([skipped]).exit_code == 0


def test_show_files_lines_align_under_the_grid_files_column():
    """--show-files continuation lines start exactly where the grid's files column starts."""
    base = _finding(type=FindingType.EXTRA, status=FindingStatus.WARN)
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(3)]

    text = str(Report(findings, show_files=True))
    lines = text.splitlines()

    row_idx = next(i for i, line in enumerate(lines) if "x3 files" in line)
    files_column_start = lines[row_idx].index("x3 files")
    continuation_line = lines[row_idx + 1]

    assert continuation_line[:files_column_start].strip() == ""
    assert continuation_line[files_column_start:].startswith("0_momma.nc")


def test_show_files_lines_align_under_the_global_attribute_block_files_column():
    """The global-attribute block's --show-files lines land under its own files column.

    Regression test: the block renders from a narrower three-column spec than the six-column
    component grid, so its continuation indent must be derived from that narrower spec too --
    reusing the component grid's continuation landed a file list well to the right of where this
    block's own ``files`` header actually starts.
    """
    base = _finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(3)]

    text = str(Report(findings, show_files=True))
    lines = text.splitlines()

    heading_idx = lines.index(f"{report._INDENT * 2}global attributes")
    header_line = lines[heading_idx + 1]
    files_header_start = header_line.index("files")

    row_idx = next(i for i, line in enumerate(lines) if "x3 files" in line)
    files_column_start = lines[row_idx].index("x3 files")
    assert files_column_start == files_header_start

    continuation_line = lines[row_idx + 1]
    assert continuation_line[:files_column_start].strip() == ""
    assert continuation_line[files_column_start:].startswith("0_momma.nc")
