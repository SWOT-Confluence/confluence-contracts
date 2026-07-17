"""Low-level NetCDF reading: the single place that understands the file format.

The one layer with no local precedent in the Confluence codebase. Everything that reads a
``.nc`` file goes through here so the format details live in exactly one module, shared by
both the reader (:mod:`cit.result`) and the contract parser (:mod:`cit.parser`).

Planned (P1-3):

- ``open_nc`` -- open a NetCDF dataset.
- ``iter_variables(ds) -> (qualified_name, numpy_dtype, dim_names, shape)`` -- walk every
  variable, descending into nested groups and qualifying names as ``group/var``.
- the single ``dtype token <-> numpy dtype`` map (e.g. ``f8``/``f4``/``i4``/``S1``), the
  bridge between a contract's ``dtype`` string and what NetCDF reports.
"""
