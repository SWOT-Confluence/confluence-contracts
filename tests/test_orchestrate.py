"""Tests for the streaming orchestrator (cit.orchestrate).

Covers loading contracts (keyed by module name) and the lazy result stream — the P1-5 acceptance
criterion that a module directory yields one result per produced file, produced one at a time.

To keep the tests independent of bundled package-data content, ``find_contract_files`` is
monkeypatched to a temp contract written by the fixture; ``find_result_files`` runs for real
against a temp mount of synthetic ``.nc`` files.
"""

from pathlib import Path

import netCDF4 as nc
import pytest

from cit.contract import Produces
from cit.orchestrate import Orchestrate
from cit.report import ValidationSource
from cit.result import NetcdfResult

CONTRACT_YAML = """\
version: "16.0"
source:
  repo: momma
  github_username: nikki-t
  branch: main
  commit: 0123456789abcdef0123456789abcdef01234567
  image_tag: momma:latest
module:
  name: momma
  produces:
    - filepath: flpe/momma/{reach_id}_momma.nc
      dimensions: [nt]
      variables:
        stage:
          dtype: f8
          dimensions: [nt]
          required: true
  consumes: []
"""

REACH_IDS = ["11111111111", "22222222222", "33333333333"]


def _write_min_nc(path: Path) -> None:
    """Write a minimal valid NetCDF file (one dim, one variable) at ``path``."""
    ds = nc.Dataset(path, "w")
    ds.createDimension("nt", 3)
    ds.createVariable("stage", "f8", ("nt",))
    ds.close()


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """An Orchestrate over a temp mount, with a temp momma contract stubbed in."""
    contract_path = tmp_path / "momma.yml"
    contract_path.write_text(CONTRACT_YAML)
    monkeypatch.setattr("cit.orchestrate.find_contract_files", lambda: [contract_path])

    momma_dir = tmp_path / "mnt" / "flpe" / "momma"
    momma_dir.mkdir(parents=True)
    for reach_id in REACH_IDS:
        _write_min_nc(momma_dir / f"{reach_id}_momma.nc")

    return Orchestrate(str(tmp_path / "mnt"))


def test_contracts_loaded_and_keyed_by_module(orch):
    """The contracts property loads and validates the contract(s), keyed by module name."""
    assert set(orch.contracts) == {"momma"}
    assert orch.contracts["momma"].module.name == "momma"


def test_iter_results_one_per_reach(orch):
    """iter_results yields one (Produces, NetcdfResult) pair per produced file (the AC)."""
    pairs = list(orch.iter_results("momma"))

    assert len(pairs) == len(REACH_IDS)
    assert all(isinstance(produces, Produces) for produces, _ in pairs)
    assert all(isinstance(result, NetcdfResult) for _, result in pairs)
    names = sorted(Path(result.filepath).name for _, result in pairs)
    assert names == sorted(f"{reach_id}_momma.nc" for reach_id in REACH_IDS)


def test_iter_results_pairs_each_result_with_its_contract_entry(orch):
    """Each result is paired with the Produces entry whose template matched it."""
    pairs = list(orch.iter_results("momma"))
    produces = orch.contracts["momma"].module.produces[0]

    assert all(entry is produces for entry, _ in pairs)


def test_iter_results_is_lazy(orch):
    """Streaming opens nothing until a result is read; each closes independently."""
    results = [result for _, result in orch.iter_results("momma")]
    assert all(result._nc._fp is None for result in results)  # constructed, none opened

    with results[0] as result:
        assert result.dimensions == {"nt": 3}  # first property access reads
        assert result._nc._fp is not None

    assert results[0]._nc._fp is None  # closed on context exit


def test_run_report_carries_contracts_into_the_banner(orch):
    """run() passes its loaded contracts into Report, so the rendered banner names momma."""
    report = orch.run()

    text = str(report)
    assert "momma" in text.splitlines()[0]
    assert "16.0" in text.splitlines()[0]


def test_run_forwards_show_passed_to_report(orch):
    """run(show_passed=True) is forwarded to Report so all-PASSED components render."""
    default_report = orch.run()
    shown_report = orch.run(show_passed=True)

    # stage is the only variable declared, and every reach file matches it exactly -> PASSED.
    assert "stage" not in str(default_report)
    assert "stage" in str(shown_report)


def test_run_forwards_checks_to_report(orch):
    """run(checks=...) is forwarded to Report, filtering which section renders.

    This fixture has no rules artifact, so momma has only structure findings: filtering to
    METADATA leaves the structure section's "stage" row out of the rendered text entirely.
    """
    structure_report = orch.run(show_passed=True, checks=ValidationSource.STRUCTURE)
    metadata_report = orch.run(show_passed=True, checks=ValidationSource.METADATA)

    assert "stage" in str(structure_report)
    assert "stage" not in str(metadata_report)
