"""I/O utilities: filesystem discovery and low-level file reads.

The only component that touches disk on the way in. It resolves bundled package data via
``importlib.resources`` and locates a module's produced files on the run mount, so the rest of the
package works with paths and parsed objects rather than reading paths directly.

Provides:

- ``find_contract_files()`` -- the bundled contract ``.yml`` resources.
- ``find_result_files(mount_path, filepath)`` -- produced files matching one ``Produces.filepath``
  template under the run mount.
- ``find_rules_files()`` -- the committed rules artifact(s) (stub until P1-15).
- ``load_yaml(path)`` -- read a YAML file into a plain dict; the EXPECTED-side low-level reader,
  counterpart to :mod:`cit.netcdf` on the ACTUAL side.
"""

from importlib.resources import files
from pathlib import Path

import yaml


def find_contract_files() -> list:
    """Return the bundled contract ``.yml`` resources, sorted by name.

    Returns:
        The ``importlib.resources`` Traversables (real paths under an editable/normal install) for
        each ``*.yml`` under ``cit/resources/contracts/``.
    """
    root = files("cit.resources").joinpath("contracts")
    return sorted(
        (p for p in root.iterdir() if p.name.endswith(".yml")),
        key=lambda p: p.name,
    )


def find_result_files(mount_path: str, filepath: str) -> list[Path]:
    """Locate the produced files for one contract path template under a run mount.

    The template's single ``{placeholder}`` becomes a ``*`` glob (e.g.
    ``flpe/momma/{reach_id}_momma.nc`` -> ``*_momma.nc``), so only the module's files match.

    Args:
        mount_path: The run mount root that contains the results tree.
        filepath: A ``Produces.filepath`` template with one ``{placeholder}``.

    Returns:
        The matching file paths, sorted.
    """
    template = Path(filepath)
    result_dir = Path(mount_path) / template.parent

    pre, _, rest = template.name.partition("{")  # "" , "{" , "reach_id}_momma.nc"
    _, _, post = rest.partition("}")  # ... , "}" , "_momma.nc"

    return sorted(result_dir.glob(f"{pre}*{post}"))  # pattern-matched, no id parsing


def find_rules_files() -> list:
    """Return the committed SoS rules artifact(s). Stub (empty) until P1-15 (``RulesValidation``)."""
    return []


def load_yaml(path: str | Path) -> dict:
    """Read a YAML file into a plain dict (low-level; no model validation).

    Args:
        path: Path to the ``.yml`` file.

    Returns:
        The parsed YAML as a dict.
    """
    return yaml.safe_load(Path(path).read_text())
