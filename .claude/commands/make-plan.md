---
description: >
  Develop a structured development plan from a user-authored context document
  or a GitHub issue. Reads the source (goals, architecture, data I/O, expected
  results), enters planning mode to reason through epics and dependencies,
  presents the plan for user review and iteration, then writes the approved
  plan to docs/plans/.
argument-hint: "<context-file-path | github-issue>  (required — e.g. docs/context/phase2.md, #5, or an issue URL)"
allowed-tools: Read Glob Bash(ls*) Bash(gh issue*) Bash(gh api*) EnterPlanMode ExitPlanMode Write
---

Turn a filled-out context document — or a GitHub issue — into a reviewable, approvable development plan, then write it to `docs/plans/` for use with `/plan-work`.

## Inputs

`$ARGUMENTS` is required and may be either:

- **A context file path** (e.g. `docs/context/phase2.md`). Use `docs/dev-context-template.md` as the template.
- **A GitHub issue** — `#5`, a bare number `5`, or a full URL (`https://github.com/SWOT-Confluence/confluence-contracts/issues/5`). Typically an epic-level issue with sub-issues, which this repo uses for its phase breakdown (e.g. `#5` "Phase 1" with `#7` "Write the structural validator" beneath it).

---

## Step 1: Read the plan source

### If `$ARGUMENTS` is a file path

```
Read <context-file-path>
```

### If `$ARGUMENTS` is a GitHub issue

Normalise it to an issue number, then fetch the issue and everything beneath it:

```
gh issue view <number> --json number,title,body,labels,milestone,url,comments
gh api repos/{owner}/{repo}/issues/<number>/sub_issues --jq '.[] | {number, title, state, body}'
```

If the `sub_issues` call fails (the API is unavailable or the issue has none), fall back to scanning the issue body for a task list of `- [ ] #NN` references and fetch each with `gh issue view`.

Read the issue body, every sub-issue, and the issue comments — design decisions often live in the comment thread rather than the body.

### Then, either way

Check that the source substantively covers:
- **Goals** — what problem this solves and what success looks like
- **Architecture** — proposed components and design decisions
- **Expected Results** — measurable or observable outcomes
- **Data I/O** — inputs and outputs with format, shape, and source/destination

A GitHub issue will rarely be structured under those exact headings — that is fine. Judge whether the substance is present, not the formatting. What matters is that you can derive epics without inventing scope.

If something material is missing or too vague to plan from, list the gaps and ask the user before proceeding. **Do not proceed with an incomplete source, and do not fill gaps by guessing.**

### Record the provenance

Whichever source was used, carry it into the plan file so `/plan-work` can trace back:

- File source → `> Source context: docs/context/phase2.md`
- Issue source → `> Source issue: SWOT-Confluence/confluence-contracts#5` plus the URL

When the source is a GitHub issue, also record each **sub-issue number against the epic it maps to**. `/plan-work` uses this to link beads issues to existing GitHub issues instead of creating duplicates.

---

## Step 2: Enter planning mode

```
EnterPlanMode
```

Reason through the full development plan before presenting anything to the user. Work through:

### 2a. Identify the epics

Break the work into epics — each epic is a coherent unit that ships as one PR. Ask yourself:

**When the source is a GitHub issue**, start from the sub-issues rather than re-deriving a breakdown: the team has usually already decided the seams, and re-cutting them silently orphans the issues people are watching. One sub-issue normally maps to one epic. Only depart from that mapping when a sub-issue is plainly too large for one PR (split it, and say so) or two are too small to review apart (merge them, and say so). Surface any departure explicitly in Step 3 so the user can veto it.

- What are the natural seams in the architecture (contract models/schema, NetCDF/JSON readers, I/O layer, structural validator, metadata-rules validator, contract parser, CLI/reporting, packaging/CI)?
- Which pieces must exist before others can start?
- Is any epic too large to review in one PR? If so, split it.
- Is any epic so small it could merge with a neighbour without losing clarity?

A good epic is: self-contained, reviewable in isolation, and leaves the codebase in a working state when merged.

### 2b. Define scope boundaries

For each epic, be explicit about what is OUT of scope. Scope boundaries prevent sub-task creep and keep developer subagents focused.

### 2c. Map data I/O to epics

For each data flow described in the context document (inputs → processing → outputs), identify which epic introduces or consumes it. Make the hand-off points between epics explicit — what does Epic N produce that Epic N+1 depends on?

### 2d. Order the epics

Build the dependency chain. Prefer a linear chain where possible (easier to manage); only introduce parallel epic branches if two epics are genuinely independent.

### 2e. Draft sub-task hints

For each epic, note 3–7 discrete units of work that will become sub-tasks when `/plan-work` runs. These are hints, not final sub-tasks — `/plan-work` will refine them.

---

## Step 3: Present the plan for review

```
ExitPlanMode
```

Print the proposed plan clearly:

```
Plan: <derived title>
Source: <context-file-path | owner/repo#N — title>

Overview
--------
<2–3 sentence summary of what this development phase delivers and why>

Data Flow
---------
<brief description of the end-to-end data flow: what goes in, what comes out, what the key transformations are>

Epics
-----
Epic 1: <title>
  GitHub: #<sub-issue> (or "none — new epic")
  Why:   <problem it solves>
  What:  <explicit deliverable list>
  Out of scope: <what is NOT included>
  Hands off to Epic 2: <what this epic produces that the next one depends on>
  Sub-task hints: <3–7 bullet points>

Epic 2: <title>  [depends on Epic 1]
  ...

Dependencies: Epic 1 → Epic 2 → Epic 3 ...

Proceed with this plan? (or describe changes)
```

**Do not write any files until the user approves.** If they request changes, re-enter planning mode, revise, and re-present.

---

## Step 4: Write the plan file

Once the user approves, derive the output path from the source:
- Context file: `docs/context/phase2.md` → Plan file: `docs/plans/phase2.md`
- GitHub issue: slugify the issue title → `docs/plans/<slug>.md` (e.g. `#5` "Phase 1: schema-level validation" → `docs/plans/phase-1-schema-level-validation.md`)
- If `docs/plans/` does not exist, create it.

Write the plan file with this structure:

```markdown
# <Plan Title>

> Source context: <context-file-path>
> Source issue: <owner/repo#N> — <url>          (include whichever applies)
> Created: <date>

## Overview

<2–3 sentence summary>

## Data Flow

<description of end-to-end data flow>

## Epics

### Epic 1: <title>

**GitHub:** #<sub-issue-number> — <sub-issue title>   *(omit if this epic has no issue yet)*

**Why:** <problem it solves>

**What this epic delivers:**
- <deliverable>
- <deliverable>

**Out of scope:** <explicit exclusions>

**Hands off to Epic 2:** <what this produces that the next epic needs>

**Sub-task hints:**
- <hint>
- <hint>

---

### Epic 2: <title>  *(depends on Epic 1)*

...

## Dependency chain

Epic 1 → Epic 2 → Epic 3 ...
```

---

## Step 5: Report back

Print:
```
Plan written: <plan-file-path>

Next step: /plan-work <plan-file-path>
```

---

## Rules

- Do not invent goals, architecture decisions, or data formats that are not in the source. If something is ambiguous, ask.
- Prefer a linear epic chain. Only branch if two epics are genuinely independent.
- Each epic must leave the codebase in a testable, non-broken state.
- Keep sub-task hints brief — they are prompts for `/plan-work`, not final issue descriptions.
- **Read only.** Do not create any beads issues — that is `/plan-work`'s job — and do not create, edit, close, or comment on any GitHub issue here. This command reads GitHub; `/plan-work` is the only command that writes to it.
- When the source is a GitHub issue, preserve its sub-issue structure unless you have an explicit reason to depart from it, and surface any departure for approval. Silently re-cutting the breakdown orphans issues the team is tracking.
