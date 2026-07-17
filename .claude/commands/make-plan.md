---
description: >
  Develop a structured development plan from a user-authored context document.
  Reads the context (goals, architecture, data I/O, expected results), enters
  planning mode to reason through epics and dependencies, presents the plan for
  user review and iteration, then writes the approved plan to docs/plans/.
argument-hint: "<context-file-path>  (required — e.g. docs/context/phase2.md)"
allowed-tools: Read Glob Bash(ls*) EnterPlanMode ExitPlanMode Write
---

Turn a filled-out context document into a reviewable, approvable development plan, then write it to `docs/plans/` for use with `/plan-work`.

## Inputs

- `$ARGUMENTS` — path to the filled-out context document (e.g. `docs/context/phase2.md`). Required. Use `docs/dev-context-template.md` as the template.

---

## Step 1: Read the context document

```
Read <context-file-path>
```

Check that all required sections are present and substantively filled out:
- **Goals** — what problem this solves and what success looks like
- **Architecture** — proposed components and design decisions
- **Expected Results** — measurable or observable outcomes
- **Data I/O** — inputs and outputs with format, shape, and source/destination

If any section is empty or too vague to plan from, list the gaps and ask the user to fill them in before proceeding. Do not proceed with an incomplete context document.

---

## Step 2: Enter planning mode

```
EnterPlanMode
```

Reason through the full development plan before presenting anything to the user. Work through:

### 2a. Identify the epics

Break the work into epics — each epic is a coherent unit that ships as one PR. Ask yourself:
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
Source: <context-file-path>

Overview
--------
<2–3 sentence summary of what this development phase delivers and why>

Data Flow
---------
<brief description of the end-to-end data flow: what goes in, what comes out, what the key transformations are>

Epics
-----
Epic 1: <title>
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

Once the user approves, derive the output path from the context file name:
- Context file: `docs/context/phase2.md` → Plan file: `docs/plans/phase2.md`
- If `docs/plans/` does not exist, create it.

Write the plan file with this structure:

```markdown
# <Plan Title>

> Source context: <context-file-path>
> Created: <date>

## Overview

<2–3 sentence summary>

## Data Flow

<description of end-to-end data flow>

## Epics

### Epic 1: <title>

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

- Do not invent goals, architecture decisions, or data formats that are not in the context document. If something is ambiguous, ask.
- Prefer a linear epic chain. Only branch if two epics are genuinely independent.
- Each epic must leave the codebase in a testable, non-broken state.
- Keep sub-task hints brief — they are prompts for `/plan-work`, not final issue descriptions.
- Do not create any beads issues — that is `/plan-work`'s job.
