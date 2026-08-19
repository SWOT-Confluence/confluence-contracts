"""Tests for the I/O utilities (cit.data).

Covers the low-level YAML reader, template-driven result discovery (the P1-5 acceptance criterion:
a ``flpe/momma/`` directory yields one file per reach), and enumeration of the bundled contract
resources. Result discovery only needs the file *paths* to exist, so these use empty ``touch``ed
files rather than real NetCDF.
"""

from pathlib import Path

from cit.data import (
    find_contract_files,
    find_result_files,
    load_yaml,
    match_result_filename,
)

MOMMA_TEMPLATE = "flpe/momma/{reach_id}_momma.nc"
SOS_TEMPLATE = "output/sos/{continent_id}_sword_v{number}_SOS_results.nc"
CONTINENTS = ("af", "as", "eu", "na", "oc", "sa")


def test_load_yaml(tmp_path):
    """load_yaml parses a YAML file into a plain dict."""
    path = tmp_path / "sample.yml"
    path.write_text("a: 1\nb: [2, 3]\n")

    assert load_yaml(path) == {"a": 1, "b": [2, 3]}


def test_find_result_files_one_per_reach(tmp_path):
    """A momma directory yields one path per reach file, sorted; non-matching files are ignored."""
    momma = tmp_path / "flpe" / "momma"
    momma.mkdir(parents=True)
    reach_ids = ["12780800061", "12590000211", "12770900011"]
    for reach_id in reach_ids:
        (momma / f"{reach_id}_momma.nc").touch()
    (momma / "12590000211_momma.jpg").touch()  # different suffix -> ignored

    found = find_result_files(str(tmp_path), "flpe/momma/{reach_id}_momma.nc")

    assert [p.name for p in found] == [f"{rid}_momma.nc" for rid in sorted(reach_ids)]
    assert all(isinstance(p, Path) and p.suffix == ".nc" for p in found)


def test_find_result_files_set_template(tmp_path):
    """A set template captures a hyphen-joined-name file (pattern genericity, no id parsing)."""
    sets = tmp_path / "flpe" / "metroman" / "sets"
    sets.mkdir(parents=True)
    (sets / "12780800041-12780800031-12780800011_metroman.nc").touch()

    found = find_result_files(str(tmp_path), "flpe/metroman/sets/{set}_metroman.nc")

    assert [p.name for p in found] == ["12780800041-12780800031-12780800011_metroman.nc"]


def test_find_result_files_empty_when_nothing_matches(tmp_path):
    """No matching files yields an empty list."""
    momma = tmp_path / "flpe" / "momma"
    momma.mkdir(parents=True)
    (momma / "notes.txt").touch()

    assert find_result_files(str(tmp_path), "flpe/momma/{reach_id}_momma.nc") == []


def test_find_result_files_matches_every_placeholder(tmp_path):
    """A template with two placeholders matches all of them, not just the first."""
    sos = tmp_path / "output" / "sos"
    sos.mkdir(parents=True)
    for continent in CONTINENTS:
        (sos / f"{continent}_sword_v17_SOS_results.nc").touch()

    found = find_result_files(str(tmp_path), SOS_TEMPLATE)

    assert [p.name for p in found] == [f"{c}_sword_v17_SOS_results.nc" for c in CONTINENTS]


def test_find_result_files_rejects_extra_trailing_segments(tmp_path):
    """The short SoS template does not match the long granule name, which is a different product."""
    sos = tmp_path / "output" / "sos"
    sos.mkdir(parents=True)
    (sos / "na_sword_v17_SOS_results.nc").touch()
    (sos / "af_sword_v17_SOS_results_20230729T153804_20260718T144212_20260724T220755.nc").touch()

    found = find_result_files(str(tmp_path), SOS_TEMPLATE)

    assert [p.name for p in found] == ["na_sword_v17_SOS_results.nc"]


def test_find_result_files_escapes_literal_dot(tmp_path):
    """A dot in the template matches only a real dot, never an arbitrary character."""
    sos = tmp_path / "output" / "sos"
    sos.mkdir(parents=True)
    (sos / "na_sword_v17_SOS_resultsXnc").touch()

    assert find_result_files(str(tmp_path), SOS_TEMPLATE) == []


def test_find_result_files_requires_a_non_empty_placeholder(tmp_path):
    """A placeholder matches one or more characters, so an absent reach id does not match."""
    momma = tmp_path / "flpe" / "momma"
    momma.mkdir(parents=True)
    (momma / "_momma.nc").touch()

    assert find_result_files(str(tmp_path), MOMMA_TEMPLATE) == []


def test_find_result_files_skips_directories(tmp_path):
    """A directory whose name matches the template is not returned as a result file."""
    momma = tmp_path / "flpe" / "momma"
    momma.mkdir(parents=True)
    (momma / "12590000211_momma.nc").mkdir()
    (momma / "12770900011_momma.nc").touch()

    found = find_result_files(str(tmp_path), MOMMA_TEMPLATE)

    assert [p.name for p in found] == ["12770900011_momma.nc"]


def test_find_result_files_missing_directory(tmp_path):
    """A module that never ran yields an empty list rather than raising."""
    assert find_result_files(str(tmp_path), MOMMA_TEMPLATE) == []


def test_find_result_files_template_without_placeholders(tmp_path):
    """A template with no placeholders matches that one exact filename."""
    sos = tmp_path / "output" / "sos"
    sos.mkdir(parents=True)
    (sos / "constants.nc").touch()
    (sos / "other.nc").touch()

    found = find_result_files(str(tmp_path), "output/sos/constants.nc")

    assert [p.name for p in found] == ["constants.nc"]


def test_match_result_filename_captures_placeholders():
    """A matching filename yields each placeholder's value, keyed by placeholder name."""
    assert match_result_filename(MOMMA_TEMPLATE, "12590000211_momma.nc") == {
        "reach_id": "12590000211"
    }
    assert match_result_filename(SOS_TEMPLATE, "af_sword_v17_SOS_results.nc") == {
        "continent_id": "af",
        "number": "17",
    }


def test_match_result_filename_returns_none_on_mismatch():
    """A filename that does not fit the template yields None, not an empty mapping."""
    assert match_result_filename(SOS_TEMPLATE, "na_sword_v17_SOS_priors.nc") is None
    assert match_result_filename(MOMMA_TEMPLATE, "12590000211_neobam.nc") is None


def test_match_result_filename_distinguishes_match_from_no_placeholders():
    """A placeholder-free template returns an empty mapping on a match -- falsy but not None."""
    matched = match_result_filename("output/sos/constants.nc", "constants.nc")

    assert matched == {}
    assert matched is not None, "callers must test `is not None`, not truthiness"


def test_find_result_files_tolerates_a_non_identifier_placeholder(tmp_path):
    """A placeholder that cannot be a regex group name still matches, just without capturing."""
    momma = tmp_path / "flpe" / "momma"
    momma.mkdir(parents=True)
    (momma / "12590000211_momma.nc").touch()

    found = find_result_files(str(tmp_path), "flpe/momma/{reach-id}_momma.nc")

    assert [p.name for p in found] == ["12590000211_momma.nc"]
    assert match_result_filename("flpe/momma/{reach-id}_momma.nc", "12590000211_momma.nc") == {}


def test_find_contract_files_lists_bundled_yml():
    """find_contract_files returns the bundled contract resources, sorted, all .yml."""
    names = [resource.name for resource in find_contract_files()]

    assert names == sorted(names)
    assert names, "expected at least one bundled contract"
    assert all(name.endswith(".yml") for name in names)
    assert "momma.yml" in names
