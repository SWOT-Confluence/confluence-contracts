# Changelog

All notable changes to this project are documented here, following
[keepachangelog.com 1.1.0](https://keepachangelog.com/en/1.1.0/) and
[semantic versioning](https://semver.org).

One bullet per epic, describing the headline change — fine-grained history lives in the commit log
and the PR descriptions.

## [Unreleased]

### Added

- **Project scaffolding:** the pip-installable `confluence-contracts` package exposing the `cit`
  command, `uv`-managed dependencies, and the ruff/pytest tooling baseline
  ([#22](https://github.com/SWOT-Confluence/confluence-contracts/pull/22)).
- **Contract models & schema:** the Pydantic v2 models describing a module contract (the EXPECTED
  side) and the deterministic JSON Schema generated from them into `schema/contract.schema.json`
  ([#23](https://github.com/SWOT-Confluence/confluence-contracts/pull/23)).
- **NetCDF reader & result model:** the single format-aware layer (`netcdf.py`) plus the lazy,
  cached read model for a produced file (the ACTUAL side), covering flat, grouped, and
  algorithm-indexed files
  ([#24](https://github.com/SWOT-Confluence/confluence-contracts/pull/24)).
- **I/O layer & streaming orchestrator:** package-data and run-mount discovery, and an orchestrator
  that streams one produced file at a time so peak memory stays at a single result
  ([#25](https://github.com/SWOT-Confluence/confluence-contracts/pull/25)).
- **Structural validator:** the self-registering `Validator` framework and `ContractValidator`,
  comparing a contract against a produced file in both directions — dimension and variable
  existence, dtype, and dimension ordering — and emitting typed `Finding`s
  ([#7](https://github.com/SWOT-Confluence/confluence-contracts/issues/7)).

### Changed

- **`VariableContract.shape` renamed to `dimensions`:** the field holds dimension *names*, not
  sizes, so the contract model now mirrors netCDF's own `Variable.dimensions`. Existing
  `contracts/*.yml` files must rename the key; `schema/contract.schema.json` regenerated
  ([#7](https://github.com/SWOT-Confluence/confluence-contracts/issues/7)).
