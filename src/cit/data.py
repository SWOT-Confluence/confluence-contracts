"""I/O layer: the only component that touches disk.

Centralizes loading so the rest of the package works with in-memory objects and never
reads a path directly. Reuses the module-name normalization conventions from
run-confluence-locally and resolves default data directories via ``importlib.resources``.

Planned (P1-5):

- ``Data.load_contract(module, contracts_dir) -> Contract`` -- parse a ``contracts/<module>.yml``.
- ``Data.load_result(path, module) -> Result | list[Result]`` -- a single file *or* a whole
  directory: globs the ``Produces.filepath`` template, returns one ``Result`` per reach, and
  dispatches to the right ``Result`` subclass by file type and module.
- ``Data.load_rules(path) -> RulesValidation`` -- load the committed rules artifact.
"""

import glob
import yaml
from importlib.resources import files
from pathlib import Path


def find_contract_files():
    """Bundled contract .yml resources, sorted by name (importlib.resources Traversables)."""
    root = files("cit.resources").joinpath("contracts")
    return sorted(
        (p for p in root.iterdir() if p.name.endswith(".yml")),
        key=lambda p: p.name,
    )


def find_result_files(mount_path: str, filepath: str) -> list[Path]:
    """Produced files matching one contract path template, sorted."""
    template = Path(filepath)
    result_dir = Path(mount_path) / template.parent

    pre, _, rest = template.name.partition("{")  # "" , "{" , "reach_id}_momma.nc"
    _, _, post = rest.partition("}")  # ... , "}" , "_momma.nc"

    return sorted(result_dir.glob(f"{pre}*{post}"))     # still pattern-matched, just no id parsing


def find_rules_files():
    """"""
    ...

def load_yaml(path) -> dict:
    """Read a YAML file into a plain dict (low-level; no model validation)."""
    return yaml.safe_load(Path(path).read_text())