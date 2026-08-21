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


def test_parent_defaults_to_empty_string():
    """Parent is optional, so a non-attribute-scoped finding need not set it."""
    finding = Finding(**_BASE, scope="variable", check=Check.EXISTS)

    assert finding.parent == ""


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

    momma_idx = lines.index(f"{report._INDENT}momma")
    output_idx = lines.index(f"{report._INDENT}output")
    assert lines[momma_idx + 1].strip() == "flpe/momma/{reach_id}_momma.nc"
    assert lines[output_idx + 1].strip() == "output/sos/{continent_id}_SOS_results.nc"


def test_nt_case_hides_the_passed_line_by_default_and_shows_it_with_show_passed():
    """The momma 'nt' case: a PASSED dimension and a non-PASSED variable share a component name.

    show_passed is a per-line rule: the PASSED dimension line does not render by default even
    though its sibling in the same component is shown, and only appears once show_passed=True.
    """
    as_dimension = _finding(component="nt", scope="dimension")
    as_variable = _finding(
        component="nt", scope="variable", type=FindingType.EXTRA, status=FindingStatus.WARN
    )

    default_text = str(Report([as_dimension, as_variable]))
    shown_text = str(Report([as_dimension, as_variable], show_passed=True))

    default_lines = default_text.splitlines()
    nt_idx = default_lines.index(f"{report._INDENT * 3}nt")
    # The shared per-file header now sits before the "nt" heading; its own rows follow directly.
    default_component_lines = default_lines[nt_idx + 1 : nt_idx + 3]
    assert not any("PASSED" in line for line in default_component_lines)
    assert any("variable" in line and "EXTRA" in line for line in default_component_lines)

    shown_lines = shown_text.splitlines()
    shown_nt_idx = shown_lines.index(f"{report._INDENT * 3}nt")
    shown_component_lines = shown_lines[shown_nt_idx + 1 : shown_nt_idx + 3]
    assert any("dimension" in line and "PASSED" in line for line in shown_component_lines)
    assert any("variable" in line and "EXTRA" in line for line in shown_component_lines)


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

    assert lines.index(f"{report._INDENT * 3}Qout") < lines.index(f"{report._INDENT * 3}zzz_extra")


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
    # component_idx + 1 is the finding row -- no header follows the heading anymore.
    component_idx = text.splitlines().index(f"{report._INDENT * 3}orphan_var")
    assert "file" not in text.splitlines()[component_idx + 1]


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

    expected_prefix = f"{report._INDENT * 4}{report._grid_continuation(report._SECTION_GRID)}"
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


def test_structure_section_precedes_metadata_section_regardless_of_severity():
    """The structure section renders before the metadata section, even when metadata is FAIL.

    Before the sections split, this was enforced by an in-component sort order; now it follows
    from :class:`ValidationSource` declaration order, since the two findings land in separate
    top-level sections rather than sharing one component's rows.
    """
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

    structure_idx = body.index("Structure checks")
    metadata_idx = body.index("Metadata checks")
    assert structure_idx < metadata_idx


def test_header_row_appears_once_per_produced_file_not_per_component():
    """One shared header row, at the finding indent, covers every component in a produced file."""
    finding_a = _finding(component="a", type=FindingType.EXTRA, status=FindingStatus.WARN)
    finding_b = _finding(component="b", type=FindingType.EXTRA, status=FindingStatus.WARN)

    body = Report([finding_a, finding_b])._render_findings()

    header = f"{report._INDENT * 4}{report._grid_header(report._SECTION_GRID)}"
    assert body.count(header) == 1


def test_shared_header_sits_directly_after_the_file_heading():
    """The shared header renders immediately after the produced-file heading, before any component."""
    finding = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)

    text = str(Report([finding]))
    lines = text.splitlines()

    file_idx = lines.index(f"{report._INDENT * 2}{finding.filepath}")
    header = f"{report._INDENT * 4}{report._grid_header(report._SECTION_GRID)}"
    assert lines[file_idx + 1] == header


def test_no_shared_header_when_only_the_global_attribute_block_survives():
    """A produced file with nothing but a global-attribute block prints no lone component header."""
    global_attribute = _finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )
    all_passed_component = _finding(component="stage")  # PASSED, filtered out by default

    body = Report([global_attribute, all_passed_component])._render_findings()

    assert "global attributes" in body
    assert report._grid_header(report._SECTION_GRID) not in body


def test_no_shared_header_when_no_findings_survive_show_passed_filtering():
    """A produced file whose only finding is filtered out by show_passed prints no lone header."""
    all_passed_component = _finding(component="stage")  # PASSED, filtered out by default

    body = Report([all_passed_component])._render_findings()

    assert body == ""


def test_header_and_rows_share_the_same_column_offsets_across_multiple_components():
    """The header's message and --show-files offsets still line up with every component's rows.

    Guards against an offset computed in one context (e.g. the file heading) and reused to
    render another -- the defect that has broken this alignment twice before, now with the
    header shared across two components rather than repeated per component.
    """
    finding_a = _finding(
        component="a", type=FindingType.DIFFERENT, status=FindingStatus.FAIL, message="detail-a"
    )
    base_b = _finding(component="b", type=FindingType.EXTRA, status=FindingStatus.WARN)
    findings_b = [replace(base_b, results_file=f"{i}_momma.nc") for i in range(3)]

    text = str(Report([finding_a, *findings_b], show_files=True))
    lines = text.splitlines()

    header = f"{report._INDENT * 4}{report._grid_header(report._SECTION_GRID)}"
    header_idx = lines.index(header)
    check_start = header.index("check")
    files_start = header.index("files")

    # header, then "a"'s heading, then a's own row, then a's message.
    a_message_line = lines[header_idx + 3]
    assert a_message_line[:check_start].strip() == ""
    assert a_message_line[check_start:] == "detail-a"

    b_row_idx = next(i for i, line in enumerate(lines) if "x3 files" in line)
    assert lines[b_row_idx].index("x3 files") == files_start
    continuation_line = lines[b_row_idx + 1]
    assert continuation_line[:files_start].strip() == ""
    assert continuation_line[files_start:].startswith("0_momma.nc")


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

    global_heading_idx = lines.index(f"{report._INDENT * 3}global attributes")
    stage_heading_idx = lines.index(f"{report._INDENT * 3}stage")
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

    assert f"{report._INDENT * 3}title" not in body.splitlines()


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

    heading_idx = lines.index(f"{report._INDENT * 3}global attributes")
    header_line = lines[heading_idx + 1]
    files_header_start = header_line.index("files")

    row_idx = next(i for i, line in enumerate(lines) if "x3 files" in line)
    files_column_start = lines[row_idx].index("x3 files")
    assert files_column_start == files_header_start

    continuation_line = lines[row_idx + 1]
    assert continuation_line[:files_column_start].strip() == ""
    assert continuation_line[files_column_start:].startswith("0_momma.nc")


def test_message_and_show_files_align_under_the_nested_attribute_grid():
    """A nested attribute sub-block's message and --show-files lines land under its own grid.

    The nested block renders one level deeper than its parent's header row (see
    _render_component_block) -- the level a hardcoded indent shift is most likely to miss, since
    the header, the row, the message offset and the continuation indent must all move together.
    Computed from the row's own text rather than a hardcoded column position, so a future depth
    change is caught here too rather than only eyeballed.
    """
    base = _finding(
        component="nodes/time.calendar",
        scope="attribute",
        check=Check.ATTRS,
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        parent="nodes/time",
        message="nested detail",
    )
    findings = [replace(base, results_file=f"{i}_momma.nc") for i in range(3)]

    text = str(Report(findings, show_files=True))
    lines = text.splitlines()

    subheading_idx = lines.index(f"{report._INDENT * 4}.calendar")
    row_line = lines[subheading_idx + 1]
    check_start = row_line.index("attrs")  # the row's own 'check' column value

    message_line = lines[subheading_idx + 2]
    assert message_line[:check_start].strip() == ""
    assert message_line[check_start:] == "nested detail"

    files_column_start = row_line.index("x3 files")
    continuation_line = lines[subheading_idx + 3]
    assert continuation_line[:files_column_start].strip() == ""
    assert continuation_line[files_column_start:].startswith("0_momma.nc")


# --- message indent, per-line PASSED filtering, and attribute nesting ----------------------


def test_message_renders_under_check_in_the_component_grid():
    """A finding's message lands under the section grid's 'check' column, not the files column."""
    finding = _finding(
        type=FindingType.DIFFERENT, status=FindingStatus.FAIL, message="mismatch detail"
    )

    text = str(Report([finding]))
    lines = text.splitlines()

    component_idx = lines.index(f"{report._INDENT * 3}stage")
    # The shared per-file header now sits one line before the component heading.
    header_line = lines[component_idx - 1]
    check_start = header_line.index("check")

    message_line = lines[component_idx + 2]
    assert message_line[:check_start].strip() == ""
    assert message_line[check_start:] == "mismatch detail"


def test_message_renders_under_found_in_the_global_attribute_block():
    """The narrower global-attribute spec puts a message under 'found', not under files."""
    finding = _finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
        message="unexpected",
    )

    text = str(Report([finding]))
    lines = text.splitlines()

    heading_idx = lines.index(f"{report._INDENT * 3}global attributes")
    header_line = lines[heading_idx + 1]
    found_start = header_line.index("found")

    message_line = lines[heading_idx + 3]
    assert message_line[:found_start].strip() == ""
    assert message_line[found_start:] == "unexpected"


def test_passed_line_hidden_by_default_even_when_its_component_has_a_fail():
    """A component with a FAIL and a PASSED shows only the FAIL line by default."""
    fail = _finding(type=FindingType.DIFFERENT, status=FindingStatus.FAIL, check=Check.DTYPE)
    passed = _finding(check=Check.DIMS)  # PASSED/INFO by default

    default_body = Report([fail, passed])._render_findings()
    shown_body = Report([fail, passed], show_passed=True)._render_findings()

    assert "PASSED" not in default_body
    assert "DIFFERENT" in default_body
    assert "PASSED" in shown_body
    assert "DIFFERENT" in shown_body


def test_attribute_findings_render_nested_under_their_variable_not_as_components():
    """An attribute finding does not render as its own top-level component."""
    own = _finding(component="nodes/time", type=FindingType.MISSING, status=FindingStatus.FAIL)
    attribute = _finding(
        component="nodes/time.calendar",
        scope="attribute",
        check=Check.ATTRS,
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        parent="nodes/time",
    )

    body = Report([own, attribute])._render_findings()
    lines = body.splitlines()

    assert f"{report._INDENT * 3}nodes/time" in lines
    assert f"{report._INDENT * 3}nodes/time.calendar" not in lines
    assert f"{report._INDENT * 4}.calendar" in lines


def test_bounds_case_renders_as_a_variable_level_row_not_a_sub_heading():
    """A bare-component attribute finding (the bounds check) gets no sub-heading of its own.

    ``Qmean_momma.constrained`` is the hazard case: the variable's own name contains a dot,
    which a naive split on "." would mistake for an attribute separator.
    """
    bounds = _finding(
        component="Qmean_momma.constrained",
        scope="attribute",
        check=Check.BOUNDS,
        type=FindingType.DIFFERENT,
        status=FindingStatus.WARN,
        parent="Qmean_momma.constrained",
    )

    body = Report([bounds])._render_findings()
    lines = body.splitlines()

    assert f"{report._INDENT * 3}Qmean_momma.constrained" in lines
    assert not any(line.strip().startswith(".") for line in lines)
    assert any("bounds" in line and "DIFFERENT" in line for line in lines)


def test_variable_with_no_own_findings_still_renders_heading_for_a_failing_attribute():
    """A variable with nothing of its own still gets a heading when an attribute of it fails."""
    attribute = _finding(
        component="stage.units",
        scope="attribute",
        check=Check.REQUIRED,
        type=FindingType.MISSING,
        status=FindingStatus.FAIL,
        parent="stage",
    )

    body = Report([attribute])._render_findings()
    lines = body.splitlines()

    assert f"{report._INDENT * 3}stage" in lines
    assert f"{report._INDENT * 4}.units" in lines


def test_variable_all_passed_including_attributes_renders_nothing():
    """A variable whose own and attribute findings are all PASSED renders nothing by default."""
    own = _finding(component="stage")
    attribute = _finding(
        component="stage.units", scope="attribute", check=Check.REQUIRED, parent="stage"
    )

    body = Report([own, attribute])._render_findings()

    assert body == ""


def test_variable_with_only_an_attribute_failure_outranks_a_warn_only_variable():
    """A variable whose sole failure sits in a nested attribute still outranks a WARN-only one.

    Component names are chosen so alphabetical order alone would put the WARN-only variable
    first -- only severity-aware ranking over the nested bucket gets this right.
    """
    warn_only = _finding(
        component="aaa_warn_only", type=FindingType.EXTRA, status=FindingStatus.WARN
    )
    fail_attribute = _finding(
        component="zzz_has_fail_attr.units",
        scope="attribute",
        check=Check.REQUIRED,
        type=FindingType.MISSING,
        status=FindingStatus.FAIL,
        parent="zzz_has_fail_attr",
    )

    body = Report([warn_only, fail_attribute])._render_findings()
    lines = body.splitlines()

    assert lines.index(f"{report._INDENT * 3}zzz_has_fail_attr") < lines.index(
        f"{report._INDENT * 3}aaa_warn_only"
    )


def test_str_is_deterministic_across_runs_with_nested_attributes():
    """Rendering with nested attribute findings is deterministic regardless of input order."""
    own = _finding(component="nodes/time", type=FindingType.MISSING, status=FindingStatus.FAIL)
    calendar = _finding(
        component="nodes/time.calendar",
        scope="attribute",
        check=Check.ATTRS,
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        parent="nodes/time",
    )
    valid_min = _finding(
        component="nodes/time.valid_min",
        scope="attribute",
        check=Check.ATTRS,
        type=FindingType.EXTRA,
        status=FindingStatus.WARN,
        parent="nodes/time",
    )

    first = str(Report([own, calendar, valid_min]))
    second = str(Report([valid_min, own, calendar]))

    assert first == second


# --- #10 follow-up: the structure/metadata section split, the --checks filter -----------------


def _metadata_finding(**overrides: object) -> Finding:
    """Build a metadata-source Finding, otherwise identical to :func:`_finding`."""
    return _finding(validation=ValidationSource.METADATA, **overrides)


def test_both_sections_render_structure_first_with_headings():
    """A run with both sources renders both headings, structure section before metadata."""
    structure = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)
    metadata = _metadata_finding(
        component="Name",
        scope="global_attribute",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )

    body = Report([structure, metadata])._render_findings()

    assert "Structure checks -- the module contract" in body
    assert "Metadata checks -- the SoS rules" in body
    assert body.index("Structure checks") < body.index("Metadata checks")


def test_structure_only_findings_render_no_metadata_section():
    """A run with only structure findings omits the metadata heading entirely."""
    structure = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)

    body = Report([structure])._render_findings()

    assert "Structure checks" in body
    assert "Metadata checks" not in body


def test_checks_structure_renders_only_the_structure_section():
    """checks=ValidationSource.STRUCTURE hides the metadata section even when it has findings."""
    structure = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)
    metadata = _metadata_finding(
        component="Name",
        scope="global_attribute",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )

    body = Report([structure, metadata], checks=ValidationSource.STRUCTURE)._render_findings()

    assert "Structure checks" in body
    assert "Metadata checks" not in body


def test_checks_metadata_renders_only_the_metadata_section():
    """checks=ValidationSource.METADATA hides the structure section even when it has findings."""
    structure = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)
    metadata = _metadata_finding(
        component="Name",
        scope="global_attribute",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )

    body = Report([structure, metadata], checks=ValidationSource.METADATA)._render_findings()

    assert "Metadata checks" in body
    assert "Structure checks" not in body


def test_checks_does_not_affect_exit_code_counts_or_csv(tmp_path: Path):
    """--checks only filters what __str__ renders -- exit_code, counts and the CSV stay whole."""
    structure_fail = _finding(
        component="stage", type=FindingType.DIFFERENT, status=FindingStatus.FAIL
    )
    metadata_warn = _metadata_finding(
        component="Name",
        scope="global_attribute",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )
    findings = [structure_fail, metadata_warn]

    whole = Report(findings)
    filtered = Report(findings, checks=ValidationSource.STRUCTURE)

    assert filtered.exit_code == whole.exit_code == 1
    assert filtered._counts_line() == whole._counts_line()

    whole_csv, filtered_csv = tmp_path / "whole.csv", tmp_path / "filtered.csv"
    whole.write_csv(whole_csv)
    filtered.write_csv(filtered_csv)
    assert whole_csv.read_text() == filtered_csv.read_text()


def test_global_attribute_block_renders_only_in_the_metadata_section():
    """The global-attribute block is metadata-only, so it never appears in the structure section."""
    global_attribute = _metadata_finding(
        scope="global_attribute",
        check=Check.EXISTS,
        component="title",
        type=FindingType.MISSING,
        status=FindingStatus.WARN,
    )
    structure = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)

    body = Report([global_attribute, structure])._render_findings()
    structure_idx = body.index("Structure checks")
    metadata_idx = body.index("Metadata checks")

    assert "global attributes" not in body[structure_idx:metadata_idx]
    assert "global attributes" in body[metadata_idx:]


def test_section_grid_drops_source_and_header_matches_the_rows_beneath_it():
    """Inside a section, the component grid has no 'source' column, and rows match its header."""
    finding = _finding(component="stage", type=FindingType.EXTRA, status=FindingStatus.WARN)

    body = Report([finding])._render_findings()
    lines = body.splitlines()

    header = f"{report._INDENT * 4}{report._grid_header(report._SECTION_GRID)}"
    header_idx = lines.index(header)
    # header_idx + 1 is the component heading; its own row follows that.
    row = lines[header_idx + 2]

    assert "source" not in header
    assert header.split()[: len(report._SECTION_GRID)] == ["scope", "check", "found", "severity"]
    assert "variable" in row and "exists" in row and "EXTRA" in row and "WARN" in row


def test_legend_and_checks_block_are_byte_identical_to_before_the_split():
    """Moving _LEGEND to the top band must not change a byte of the rendered legend text.

    Captured verbatim from the report before the section split (when _LEGEND was a single
    f-string ending in ``_checks_block()``), so a change to either constant's *text* -- not just
    where it is assembled -- fails this test.
    """
    assembled = f"{report._LEGEND}\n\n{report._checks_block()}"

    assert assembled == (
        "Declared = what the contract (structure) or the SoS rules (metadata) say should be "
        "there.\n"
        "Found    = what the produced file actually holds.\n"
        "\n"
        "PASSED     Declared and found, contract/rules match module file.\n"
        "MISSING    Declared, but not found in the file. Data is missing from the module file.\n"
        "EXTRA      Found in the file, but not declared. Extra data located in the module file.\n"
        "DIFFERENT  Declared and found, contract/rules do not match module file. Message "
        "indicates\n"
        "           values for both.\n"
        "SKIPPED    CIT could not run this check -- a gap in the tool, not in the data.\n"
        "\n"
        "Checks -- the question each line asked.\n"
        "\n"
        "structure   compared against the module's contract (contracts/<module>.yml)\n"
        "  exists      the dimension or variable is declared and present in the file\n"
        "  dtype       the variable's data type matches the one declared\n"
        "  dims        the variable's dimension names, and their order, match\n"
        "  dtype+dims  both agreed -- reported once instead of two PASSED lines\n"
        "\n"
        "metadata    compared against the SoS rules (rules/sos_results_rules.yml)\n"
        "  exists      the spec covers this variable, or requires this global attribute\n"
        "  attrs       the variable carries the attribute names the spec declares for it\n"
        "  required    long_name, units and coverage_content_type are present and non-blank\n"
        "  bounds      valid_min is not greater than valid_max\n"
        "  fill        the fill value is the canonical one for the variable's data type"
    )
