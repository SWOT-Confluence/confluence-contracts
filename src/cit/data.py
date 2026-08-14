"""I/O utilities: filesystem discovery and low-level file reads.

The only component that touches disk on the way in. It resolves bundled package data via
``importlib.resources`` and locates a module's produced files on the run mount, so the rest of the
package works with paths and parsed objects rather than reading paths directly.

Provides:

- ``find_contract_files()`` -- the bundled contract ``.yml`` resources.
- ``find_result_files(mount_path, filepath)`` -- produced files matching one ``Produces.filepath``
  template under the run mount.
- ``match_result_filename(filepath, name)`` -- the placeholder values a filename supplies, or
  ``None`` when it does not match the template.
- ``find_rules_files()`` -- the committed rules artifact(s) (stub until P1-15).
- ``load_yaml(path)`` -- read a YAML file into a plain dict; the EXPECTED-side low-level reader,
  counterpart to :mod:`cit.netcdf` on the ACTUAL side.

A ``Produces.filepath`` is a template whose ``{placeholder}`` segments stand for the parts that
vary per file -- a reach id, a continent, a SWORD version. Templates carry anywhere from one
placeholder (``flpe/momma/{reach_id}_momma.nc``) to several
(``output/sos/{continent_id}_sword_v{number}_SOS_results.nc``), so each is compiled to an anchored
regex rather than reduced to a glob: a glob cannot express "every placeholder" without collapsing
them all to ``*``, which both over-matches and discards the values. Matching keeps them, so a
caller can recover the reach id or continent a file belongs to.
"""

import re
from importlib.resources import files
from pathlib import Path

import yaml

# One {placeholder} in a Produces.filepath template.
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


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


def _compile_template(name_template: str) -> re.Pattern:
    """Compile a filename template into an anchored regex, one group per placeholder.

    Every ``{placeholder}`` becomes ``[^/]+`` -- one or more characters that are not a path
    separator -- and is captured under the placeholder's own name when that name is a valid
    Python identifier, so a caller can read the values back out. Literal text between
    placeholders is escaped, so a ``.`` in a template matches only a real dot.

    Args:
        name_template: The filename part of a ``Produces.filepath`` template, e.g.
            ``{continent_id}_sword_v{number}_SOS_results.nc``.

    Returns:
        A compiled pattern to use with :meth:`re.Pattern.fullmatch`.
    """
    pattern = ""
    position = 0

    for placeholder in _PLACEHOLDER.finditer(name_template):
        pattern += re.escape(name_template[position : placeholder.start()])
        name = placeholder.group(1)
        pattern += f"(?P<{name}>[^/]+)" if name.isidentifier() else "[^/]+"
        position = placeholder.end()

    return re.compile(pattern + re.escape(name_template[position:]))


def match_result_filename(filepath: str, name: str) -> dict[str, str] | None:
    """Return the placeholder values a filename supplies, or None if it does not match.

    Args:
        filepath: A ``Produces.filepath`` template.
        name: A bare filename (no directory part) to test against it.

    Returns:
        A mapping of placeholder name to the matched text (empty when the template has no named
        placeholders), or ``None`` when the filename does not match the template.
    """
    matched = _compile_template(Path(filepath).name).fullmatch(name)
    return matched.groupdict() if matched else None


def find_result_files(mount_path: str, filepath: str) -> list[Path]:
    """Locate the produced files for one contract path template under a run mount.

    The template may hold any number of ``{placeholder}`` segments;
    ``flpe/momma/{reach_id}_momma.nc`` and
    ``output/sos/{continent_id}_sword_v{number}_SOS_results.nc`` are both matched exactly. Only
    the template's own directory is listed -- there is no recursion, because a contract names the
    directory its module writes to.

    Args:
        mount_path: The run mount root that contains the results tree.
        filepath: A ``Produces.filepath`` template.

    Returns:
        The matching file paths, sorted. Empty when the directory does not exist, so a module that
        never ran is reported as producing nothing rather than raising.
    """
    template = Path(filepath)
    result_dir = Path(mount_path) / template.parent

    if not result_dir.is_dir():
        return []

    matcher = _compile_template(template.name)

    return sorted(
        path for path in result_dir.iterdir() if path.is_file() and matcher.fullmatch(path.name)
    )


def find_rules_files() -> list:
    """Return the committed SoS rules artifact(s), sorted by name.

    Returns:
        The ``importlib.resources`` Traversables (real paths under an editable/normal install) for
        each ``*.yml`` under ``cit/resources/rules/``.
    """
    root = files("cit.resources").joinpath("rules")
    return sorted(
        (p for p in root.iterdir() if p.name.endswith(".yml")),
        key=lambda p: p.name,
    )

def load_yaml(path: str | Path) -> dict:
    """Read a YAML file into a plain dict (low-level; no model validation).

    Args:
        path: Path to the ``.yml`` file.

    Returns:
        The parsed YAML as a dict.
    """
    return yaml.safe_load(Path(path).read_text())
