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
