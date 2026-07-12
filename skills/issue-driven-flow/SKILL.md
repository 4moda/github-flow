---
name: issue-driven-flow
description: >-
  Issue-driven AI development on GitHub. Humans steer with one public label
  (`flow`) and one checkbox (`ready for implementation`); reusable GitHub
  Actions workflows run a coding agent (Claude Code, Codex, or Gemini CLI)
  to shape issues (Composer) and implement them as pull requests (Crafter).
  Merging is always a human action.
metadata:
  version: 0.1.0
  category: automation
---

# issue-driven-flow — issue-driven AI execution for GitHub

`issue-driven-flow` turns GitHub Issues into a task pipeline that a coding
agent (Claude Code, Codex, or Gemini CLI — selected by the credential the
consumer provides) works through inside GitHub Actions. It lives in one
shared repository; product repositories consume it with a single thin
wrapper workflow.

## Operating model in one paragraph

A human opens an issue and adds the **`flow`** label whenever they want the
next automated step to run. The first run **shapes** the issue: the Composer
rewrites it into a fixed template with acceptance criteria. The human
reviews the shaped issue, ticks **`ready for implementation`**, and adds
`flow` again; the Crafter implements the issue on a branch and opens a pull
request. Humans review and merge — automation never merges. Progress is
tracked with internal `flow/*` state labels that only the workflows touch.

## Control surface

| Who | Touches | Meaning |
|-----|---------|---------|
| Human | `flow` label on an issue | "run the next step now" |
| Human | `flow` label on a `flow/issue-<n>` PR | "rework this PR per its review feedback" (equivalent to labeling the issue) |
| Human | `ready for implementation` checkbox | approval to implement |
| Automation | `flow/*` labels | current state; at most one per issue |

Humans never add or remove `flow/*` labels. Every `flow` trigger is answered
exactly once — with a run or with an explanatory comment — and the `flow`
label is always removed by automation afterwards. The trigger label name is
configurable per repository (`trigger_label` input; default `flow`, and it
must not start with `flow/` — that prefix is the state namespace).

## Roles

| Role | Runs in | Contract |
|------|---------|----------|
| **Composer** | `shape.yml` | [references/composer.md](references/composer.md) — rewrites the issue into the fixed template; never touches code |
| **Crafter** | `build.yml` | [references/crafter.md](references/crafter.md) — edits the working tree; the workflow commits, pushes, and opens the PR |

There is no AI reviewer role. Review and merge are human responsibilities.

Both roles follow the same execution pattern: the workflow collects context
into files, the agent reads its contract and writes result files, and the
workflow applies those results to GitHub deterministically. The agents never
call the GitHub API themselves. Each role can also leave handoff notes
(`notes.md`): the workflow persists them as one auto-managed comment per
role on the issue and feeds them back to that role's next run, so re-runs
keep continuity without hidden session state.

## Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Raw : issue created (no flow label)
    Raw --> Shaping : human adds flow
    Shaping --> AwaitingApproval : Composer shaped the issue
    Shaping --> Split : too large — sub-issues created instead
    Split --> [*]
    Shaping --> BlockedShape : Composer needs human input
    BlockedShape --> Shaping : human answers + adds flow
    AwaitingApproval --> Shaping : human adds flow with checkbox unticked (re-shape)
    AwaitingApproval --> Building : checkbox ticked + human adds flow
    Building --> PrOpen : branch pushed, PR opened/updated
    Building --> BlockedBuild : Crafter needs human input / run failed
    BlockedBuild --> Building : human answers + adds flow
    PrOpen --> Building : rework requested + human adds flow
    PrOpen --> Done : human merges PR
    PrOpen --> BlockedBuild : PR closed without merge
    Done --> [*]
```

State semantics, invariants, and edge cases:
[references/concepts.md](references/concepts.md)

## Workflows

| Workflow | Trigger (in consumer repo) | Acts when | Does |
|----------|---------------------------|-----------|------|
| `shape.yml` | `issues: [labeled]` with `flow` | no state label, `flow/blocked-shape`, or `flow/awaiting-approval` with checkbox unticked | runs Composer; rewrites the issue body, or splits an oversized issue into shaped sub-issues; also acknowledges `flow` in states no workflow handles |
| `build.yml` | `issues: [labeled]` or `pull_request: [labeled]` with `flow` | `flow/awaiting-approval` + checkbox ticked, `flow/blocked-build`, `flow/pr-open` | runs Crafter, commits/pushes `flow/issue-<n>`, opens or updates the PR; acknowledges PR-labeled triggers it cannot act on |
| `sync-pr.yml` | `pull_request: [closed, reopened]`, `pull_request_review: [submitted]` | PR head branch is `flow/issue-<n>` | mirrors merge/close/reopen/changes-requested back to the issue — mechanical, no AI |

Routing between shape and build is a single tested function
(`scripts/gf.py route`), so exactly one workflow responds to any `flow`
trigger.

## Consuming from another repository

Consumers add one wrapper workflow, the labels, and an API credential.
Complete instructions with a copy-paste wrapper:
[docs/adopting.md](../../docs/adopting.md)

## Repository layout

```
skills/issue-driven-flow/       this skill and the agent contracts
  references/composer.md    Composer (shaping) contract
  references/crafter.md     Crafter (implementation) contract
  references/issue-template.md  shaped-issue body format
  references/concepts.md    state machine and invariants
.github/workflows/        reusable workflows (shape, build, sync-pr) + CI
actions/                  composite actions (route, build-context, update-issue)
scripts/                  gf.py decision logic, setup-labels.sh
tests/                    unit tests for gf.py
docs/adopting.md          consumer setup guide
```

## Design rules

1. **Merge is always human.** No workflow or agent merges, ever.
2. **At most one `flow/*` label per issue** — enforced by `update-issue`,
   which is the only writer of state labels.
3. **Deterministic apply.** Agents write files; workflows apply them with
   `gh`. Agents never mutate GitHub state directly.
4. **Every `flow` add gets exactly one response** (a run or a comment), and
   automation removes the label afterwards.
5. **Blocked states always come with a comment** saying precisely what human
   input is missing and how to resume.
6. **AI runs in exactly two places** — the Composer step of `shape.yml` and
   the Crafter step of `build.yml`. Routing, label management, checkbox
   detection, sub-issue creation, PR publishing, and merge/close sync are
   all deterministic scripts (`gf.py` + `gh`), never model calls.
