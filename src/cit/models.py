"""Pydantic v2 models describing a module contract (the EXPECTED side).

These models are the in-memory representation of a ``contracts/<module>.yml`` file:
the declared interface a Confluence module promises to produce. They are the source
of truth from which the committed JSON Schema is derived (see :mod:`cit.schema`).

Planned classes (P1-2), outer to inner:

- ``Contract`` -- top-level document: module, confluence version, and source provenance.
- ``ModuleContract`` -- the module's name plus what it ``produces`` and ``consumes``.
- ``Produces`` -- one output file: path template, dimensions, and variables.
- ``Consumes`` -- inputs a module reads (feeds the consumes/produces cross-check).
- ``VariableContract`` -- one variable's structure: dtype, shape, required.
- ``VariableAttrs`` -- SoS metadata attributes linted by :mod:`cit.rules`.
- ``Source`` -- provenance: repo, github_username, branch, commit, image_tag.

All models use ``extra="forbid"`` so an unexpected key is an error, not a silent typo.
"""
