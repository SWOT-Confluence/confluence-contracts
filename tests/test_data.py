"""Tests for the I/O utilities (cit.data).

Covers the low-level YAML reader, template-driven result discovery (the P1-5 acceptance criterion:
a ``flpe/momma/`` directory yields one file per reach), and enumeration of the bundled contract
resources. Result discovery only needs the file *paths* to exist, so these use empty ``touch``ed
files rather than real NetCDF.
"""

from pathlib import Path

from cit.data import find_contract_files, find_result_files, load_yaml


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


def test_find_contract_files_lists_bundled_yml():
    """find_contract_files returns the bundled contract resources, sorted, all .yml."""
    names = [resource.name for resource in find_contract_files()]

    assert names == sorted(names)
    assert names, "expected at least one bundled contract"
    assert all(name.endswith(".yml") for name in names)
    assert "momma.yml" in names
