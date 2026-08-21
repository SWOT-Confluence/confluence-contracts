# Changelog

All notable changes to this project are documented here, following
[keepachangelog.com 1.1.0](https://keepachangelog.com/en/1.1.0/) and
[semantic versioning](https://semver.org).

One bullet per epic, describing the headline change — fine-grained history lives in the commit log
and the PR descriptions.

## [Unreleased]

### Added

- **Project scaffolding:** CIT is now a pip-installable Python package, `confluence-contracts`,
  that puts a `cit` command on your PATH. Its dependencies and its ruff and pytest tooling are
  managed with `uv` ([#22](https://github.com/SWOT-Confluence/confluence-contracts/pull/22)).
- **Contract models and schema:** A contract is the YAML file that declares what a Confluence
  module's result files are supposed to contain, and Pydantic models now define and validate that
  structure. A JSON Schema is generated from those models into `schema/contract.schema.json`, so an
  editor or a CI job can check a contract before CIT ever runs
  ([#23](https://github.com/SWOT-Confluence/confluence-contracts/pull/23)).
- **NetCDF reader and result model:** CIT can now open a result file a module produced and read
  back its dimensions, variables and attributes, handling flat, grouped and algorithm-indexed
  layouts alike. Reads are lazy and cached, so nothing is pulled off disk until a check asks for it
  ([#24](https://github.com/SWOT-Confluence/confluence-contracts/pull/24)).
- **File discovery and streaming orchestrator:** CIT now locates the contracts bundled inside the
  installed package and the matching result files under a run mount on disk. It validates one file
  at a time rather than loading them all, so memory use stays flat however many files a run
  produced ([#25](https://github.com/SWOT-Confluence/confluence-contracts/pull/25)).
- **Structural validator:** The first validator compares a contract against a produced file in both
  directions, reporting anything that is declared but missing, present but undeclared, or
  mismatched in data type or dimension order. Validators register themselves, so adding another
  kind of check does not mean changing the code that runs them
  ([#7](https://github.com/SWOT-Confluence/confluence-contracts/issues/7)).
- **SoS metadata rules artifact:** The metadata required of the SWORD of Science (SoS) dataset that
  Confluence publishes to NASA's PO.DAAC archive is maintained by the science team in a
  spreadsheet, and `tools/rules_convert.py` converts that spreadsheet into a YAML artifact shipped
  with the package. A test re-runs the conversion and fails if the committed artifact has drifted,
  so it is never hand-edited
  ([#8](https://github.com/SWOT-Confluence/confluence-contracts/issues/8)).
- **SoS metadata-rules validator:** A second validator checks a produced file's metadata against
  those rules — the global attributes every file must carry, the per-variable attribute *names*
  such as `long_name` and `units` the spreadsheet declares for it (not their values — the only
  value-level checks are `valid_min`/`valid_max` ordering and canonical fill values), and whether
  a variable's fill value is the canonical one for its data type. Violations are reported as
  warnings by default and become failures under `--strict`, and the first contract for the
  aggregated SoS results file ships alongside as `contracts/output.yml`
  ([#9](https://github.com/SWOT-Confluence/confluence-contracts/issues/9)).
- **Findings report and a working `cit validate`:** `cit validate --results <mount>` now checks a
  run's files against their contracts and prints a report split into a structure section and a
  metadata section (in that order), each grouped by module, then produced file, then component,
  collapsing a finding that recurs across many files into one line and exiting 1 if anything
  failed. A new `--checks structure|metadata|all` flag (default `all`) renders just one section
  without changing the exit code, the counts line, or `--csv`, so a filtered view can never
  disagree with the run's real outcome. Every line names its scope, specific check, verdict and
  severity across a grid with a header row per component -- the source is no longer a column
  since the section heading already says it; global attributes render in their own compact block
  above a file's components (metadata-only, so it only ever appears in that section); a variable's
  attribute-level findings nest beneath it instead of rendering as separate components; and the one
  check CIT cannot run at all (a fill value for an unmapped dtype) reports as `SKIPPED`/`REPORT`
  instead of a data disagreement. A mismatch message sits under the grid's `check` column rather
  than the wider `files` column. `--show-passed` is a per-line rule -- a PASSED line renders only
  when it is set, never merely because a sibling in the same component did not pass --
  `--show-files` names the files behind a finding (capped by `--max-files`), and `--report` and
  `--csv` save the text report and a full one-row-per-occurrence export (the CSV header now
  carries the report's `scope`, `check` and `parent` columns too)
  ([#10](https://github.com/SWOT-Confluence/confluence-contracts/issues/10)).

### Changed

- **A variable's `shape` field is now called `dimensions`:** The field lists dimension *names*
  rather than sizes, so it now carries the same name netCDF itself uses. Every existing contract
  file must rename the key, and `schema/contract.schema.json` has been regenerated to match
  ([#7](https://github.com/SWOT-Confluence/confluence-contracts/issues/7)).
