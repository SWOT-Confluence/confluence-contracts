---
name: changelog
description: Update CHANGELOG.md and apply semantic versioning. Use when creating a PR, adding changelog entries, or cutting a release.
---

`CHANGELOG.md` is a **top-level, human-readable snapshot of the codebase** — read top-to-bottom, it tells you what each epic delivered. Keep it coarse: **one line (one bullet) per epic**, describing the headline change — *not* a per-sub-task or per-commit log. Fine-grained history lives in commit messages and the PR description; the changelog is the quick "state of the project" view.

Update it at the **epic / PR level** following the [keepachangelog.com 1.1.0](https://keepachangelog.com/en/1.1.0/) format.

**Structure:**

```markdown
## [Unreleased]

## [X.Y.Z] - YYYY-MM-DD
### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
```

**Rules:**
- **One bullet per epic.** Lead with the epic name in bold, then a single wrapped sentence of headline deliverables — e.g. `**Structural validator:** contract-vs-file comparison of dimensions, dtypes, and variable existence, emitting typed findings.` Never add a bullet per sub-task or per file.
- **Fold, don't append.** While an epic is still under `[Unreleased]`, fold its later refinements into that epic's existing bullet rather than adding new bullets — the line always reflects the epic's net current state.
- Keep an `## [Unreleased]` section at the top for epics staged but not yet tagged.
- When cutting a release, rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD` and open a fresh `[Unreleased]` above it.
- Latest version always appears first.
- Only include sub-sections that have entries — omit empty ones.
- File each epic under the sub-section matching its net effect (usually **Added** for a new epic; **Changed** when it modifies already-released behaviour). Link to the PR and the GitHub issue where helpful.
- Keep lines around 100 characters wide (matching the ruff line length).
- Sub-section meanings:
  - **Added** — new features, modules, CLI subcommands or flags, a new validator, a new bundled contract
  - **Changed** — modifications to existing behaviour (refactors, renamed contract fields, API updates)
  - **Deprecated** — features that will be removed in a future release
  - **Removed** — deleted code, dropped flags, removed files
  - **Fixed** — bug fixes
  - **Security** — vulnerability patches
- The beads queue is the authoritative source of what to do next — `CHANGELOG.md` is the authoritative record of what was done.

**What counts as a user-visible change here.** CIT's consumers are the Confluence module developers who run `cit` and the CI that gates their PRs. So a changelog entry is warranted when a change alters: what `cit` accepts or reports, the contract schema, a bundled `contracts/*.yml` declared interface, the exit-code policy, or the CLI surface. Pure internal refactors that leave all of those identical do not need an entry.

**Cutting a release:**
1. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (version per the semver rules below).
2. Bump `version` in `pyproject.toml` to match.
3. Open a fresh `## [Unreleased]` above it.
4. Tag the commit: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
5. Once tagged, a released section is immutable — never edit it; fix forward with a new version.

**Semantic versioning ([semver.org](https://semver.org), v2.0.0):** `MAJOR.MINOR.PATCH`

| Segment | Increment when… |
|---------|----------------|
| `MAJOR` | a breaking / incompatible change (e.g. a contract-schema change that invalidates existing `contracts/*.yml`, a renamed or removed `Finding` field, a dropped CLI subcommand or flag, a check that newly FAILs what previously passed) |
| `MINOR` | new backward-compatible functionality (e.g. a new validator, a new bundled module contract, a new optional contract field, a new CLI flag) |
| `PATCH` | a backward-compatible bug fix (e.g. a mis-classified finding status, a dtype token mapped wrongly, a test-infra fix) |

Additional rules:
- While in initial development, stay on `0.y.z` — anything may change; `1.0.0` signals a stable contract schema and CLI that downstream modules can rely on.
- **A contract-schema change is the one to watch.** `schema/contract.schema.json` is consumed by editors and by future `confluence_interfaces` users, so adding a required field or renaming one is MAJOR even if the Python side still round-trips. Adding an optional field is MINOR.
- **Tightening a check is breaking.** If a run that previously exited 0 now exits 1 without the module changing, that is MAJOR — module CI will start failing on unchanged code. Landing the check as a WARN first, then promoting it, avoids this.
- Pre-release suffixes use a hyphen: `0.3.0-alpha`, `0.3.0-rc.1`.
- Each version number must increase numerically; never re-use or skip a version.
