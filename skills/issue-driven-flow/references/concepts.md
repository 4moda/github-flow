# issue-driven-flow concepts

The state machine, its invariants, and the edge-case behavior that the
workflows implement. `scripts/gf.py` is the single source of truth for the
routing table below; if this document and the code disagree, fix one of
them.

## Actors

- **Human** — opens issues, adds the `flow` label, ticks the ready checkbox,
  reviews and merges PRs. Never touches `flow/*` labels.
- **Composer** — agent run that shapes an issue into the fixed template.
  Never touches code.
- **Crafter** — agent run that implements a shaped issue in the working
  tree. Never commits, pushes, or merges.
- **Workflows** — own all GitHub state: `flow/*` labels, issue bodies,
  comments, branches, PRs, and issue open/closed status. Everything they
  apply comes from agent result files or event payloads, deterministically.

## States

A state is an issue label starting with `flow/`. An issue carries **at most
one**. No label means the issue has not entered the flow yet ("Raw" — there
is deliberately no `flow/raw` label, so new issues need no setup).

| State | Meaning | Set by |
|-------|---------|--------|
| *(none)* | not in the flow yet | — |
| `flow/shaping` | Composer run in progress | shape.yml |
| `flow/awaiting-approval` | shaped; waiting for a human to tick the checkbox | shape.yml |
| `flow/building` | Crafter run in progress | build.yml |
| `flow/pr-open` | a PR on `flow/issue-<n>` is open | build.yml, sync-pr.yml |
| `flow/blocked-shape` | shaping needs human input (or the run failed) | shape.yml |
| `flow/blocked-build` | implementation needs human input (or the run failed, or the PR was closed unmerged) | build.yml, sync-pr.yml |
| `flow/done` | PR merged; flow complete | sync-pr.yml |
| `flow/split` | split into sub-issues; tracking only, never implemented directly | shape.yml, cleared by sync-pr.yml once every sub-issue closes |

`flow/shaping` and `flow/building` exist only while a run is executing; the
run always exits them (to a result state, or to a blocked state via the
failure handler).

## Routing table

When a human adds `flow` **to an issue**, both shape.yml and build.yml
receive the event and ask `gf.py route` what to do. For any issue, exactly
one of them acts:

| Condition | shape.yml | build.yml |
|-----------|-----------|-----------|
| payload is a pull request | skip | skip |
| multiple `flow/*` labels | acknowledge (invalid) | skip |
| issue closed | acknowledge | skip |
| *(none)* / `flow/blocked-shape` | **shape** | skip |
| `flow/awaiting-approval`, checkbox unticked | **shape** (re-shape) | skip |
| `flow/awaiting-approval`, checkbox ticked | skip | **build** (first run) |
| `flow/blocked-build` / `flow/pr-open` | skip | **build** |
| `flow/shaping` / `flow/building` | acknowledge (busy) | skip |
| `flow/done` | acknowledge (done) | skip |
| `flow/split` | acknowledge (use the sub-issues) | skip |
| unknown `flow/*` label | acknowledge (unknown) | skip |

"Acknowledge" posts a comment explaining why nothing ran and removes `flow`,
so the trigger label never lingers silently.

The re-shape row resolves scope rewrites: while the checkbox is unticked,
`flow` always means "shape (again)". Ticking the checkbox is the only thing
that changes the meaning of `flow` to "implement".

### PR trigger (rework shortcut)

`flow` added **to a `flow/issue-<n>` pull request** reaches only build.yml
(shape.yml ignores PR events). The linked issue is derived from the branch
name, then the same routing applies with build.yml owning the acknowledge
role: buildable states run the Crafter (which reads the PR reviews and
comments), every other state gets an explanatory comment on the PR and the
label is removed. So "leave review feedback on the PR, label the PR" and
"label the issue" are equivalent gestures.

## Happy path walkthrough

1. Human opens an issue describing a change, adds `flow`.
2. shape.yml: sets `flow/shaping`, removes `flow`, collects context, runs the
   Composer, replaces the issue body with the shaped template, sets
   `flow/awaiting-approval`, comments with instructions.
3. Human reviews the shaped issue, optionally edits it, ticks
   `ready for implementation`, adds `flow`.
4. build.yml: sets `flow/building`, removes `flow`, prepares branch
   `flow/issue-<n>`, runs the Crafter, commits and pushes the changes, opens a
   PR with `Closes #<n>`, sets `flow/pr-open`, comments with the PR link.
5. Human reviews the PR.
   - Satisfied → merges. sync-pr.yml sets `flow/done`; GitHub closes the
     issue via `Closes #<n>`.
   - Wants changes → leaves a review requesting changes (sync-pr.yml
     comments a reminder) and adds `flow` to the PR or the issue; build.yml
     reruns the Crafter on the same branch and PR with the review feedback
     in context.

## Split (oversized issues)

When a raw issue is too large for one reviewable PR, the Composer reports
`split` instead of `shaped`. The workflow then mechanically creates 2–8
sub-issues (each already in the shaped template, state
`flow/awaiting-approval`), rewrites the parent into a tracking overview
with a `## Sub-issues` checklist, and sets the parent to `flow/split`. Each
sub-issue body also carries a hidden marker
(`<!-- issue-driven-flow:split-parent:<n> -->`) recording its parent, so a
closed sub-issue can be traced back automatically. Each sub-issue is also
registered with the parent through GitHub's native Sub-issues API, so issue
lists, project boards, and the API itself show the parent/child relationship
without reading either issue's body; a failure of that API call does not
block sub-issue creation, and completion is still tracked solely through the
checklist below. Humans approve and trigger each sub-issue individually; the
parent's checklist ticks itself as sub-issues close (GitHub's native
tasklist behavior). `flow` on a `flow/split` parent is acknowledged with a
pointer to the sub-issues.

Once every sub-issue listed in the parent's checklist is closed, sync-pr.yml
(triggered by the `issues: closed` event, mechanically — no AI) closes the
parent and sets it to `flow/done` with a comment stating the split is
complete. While at least one sub-issue is still open, the parent is left
untouched — no partial updates. A sub-issue closing with no split-parent
marker (an ordinary issue) is a no-op for this check.

## What sync-pr does (and does not) react to

sync-pr.yml is purely mechanical — no AI. It runs only on:

- `pull_request` `closed` — merged → issue `flow/done`; not merged →
  issue `flow/blocked-build`.
- `pull_request` `reopened` — issue back to `flow/pr-open`.
- `pull_request_review` `submitted` — only `changes_requested` reviews get
  a rework-guidance comment on the issue; approvals and plain comment
  reviews are no-ops.
- `issues` `closed` — resolves the closed issue's `flow/split` parent (if
  any) and, when every sub-issue listed in that parent's checklist is now
  closed, closes the parent and sets it to `flow/done`. A no-op for issues
  with no split parent, and for parents with sub-issues still open.

Ordinary conversation comments (`issue_comment`) never trigger it, and PRs
whose head branch is not `flow/issue-<n>` are ignored entirely.

## Blocked loops

- Composer blocked → `flow/blocked-shape` + a comment with concrete
  questions. Human answers (issue edit or comment) and adds `flow` → shaping
  reruns with the answers in context.
- Crafter blocked → `flow/blocked-build` + a comment. Same resume gesture.
- Any run that fails unexpectedly lands in the corresponding blocked state
  with a link to the run log; adding `flow` retries.

## Invariants

1. At most one `flow/*` label per issue. `actions/update-issue` is the only
   writer of state labels and enforces this on every transition.
2. Merge is always performed by a human. Nothing in this repository calls a
   merge API.
3. Automation never ticks the `ready for implementation` checkbox.
4. Every `flow` add is answered exactly once, and automation removes `flow`.
5. Agents only write files; workflows apply them. An agent result that is
   missing or malformed is treated as a failed run, never guessed at.
6. One issue ↔ one branch (`flow/issue-<n>`) ↔ one open PR. The branch name is
   the link; no fragile cross-reference parsing.

## Edge cases

- **`flow` while a run is in progress** — concurrency serializes runs per
  issue; the queued run sees `flow/shaping`/`flow/building` and acknowledges.
- **`flow` on a done or closed issue** — acknowledged with guidance to open a
  new issue.
- **Multiple `flow/*` labels** (manual tampering) — acknowledged; the human
  is asked to remove the extras.
- **PR closed without merging** — issue moves to `flow/blocked-build` with
  instructions to rebuild or abandon.
- **PR reopened** — issue moves back to `flow/pr-open`.
- **Crafter reports success with no changes** — nothing is pushed; the issue
  moves to `flow/blocked-build` explaining that the acceptance criteria may
  already be satisfied.
- **Last sub-issue of a split closes** — the `flow/split` parent is closed
  and set to `flow/done` automatically; a previously-closed sub-issue being
  reopened later does not reopen the parent (out of scope, a human
  decision).
