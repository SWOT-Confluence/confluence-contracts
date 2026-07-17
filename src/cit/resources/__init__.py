"""Bundled package data for cit (loaded via importlib.resources).

Holds the runtime data shipped inside the wheel: ``contracts/`` (per-module contract
YAML) and ``rules/`` (the generated SoS rules artifact). Access files with
``importlib.resources.files("cit.resources").joinpath(...)`` so resolution is identical
under editable and installed modes.
"""
