# Adopting issue-driven-flow in a repository

[English](adopting.md) | [日本語](adopting.ja.md)

Three one-time steps: allow Actions to create PRs, add a credential, and
create the labels — then drop in one wrapper workflow.

`4moda/issue-driven-flow` is public, so its reusable workflows and actions are
usable from any repository as-is. (If you run a private fork instead, grant
access first: fork → **Settings → Actions → General → Access** →
*"Accessible from repositories owned by ..."*.)

## 1. Allow GitHub Actions to create pull requests

Unless you provide `GF_BOT_TOKEN` (below), the Crafter opens PRs with the
default `GITHUB_TOKEN`, and GitHub blocks that by default — the build run
fails at PR creation with *"GitHub Actions is not permitted to create or
approve pull requests"*. Enable it in the consumer repository:

- **Settings → Actions → General → Workflow permissions** → tick
  *"Allow GitHub Actions to create and approve pull requests"*, or

```bash
gh api -X PUT repos/<owner>/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=true
```

(For organizations, the same toggle also exists at the org level and caps
the repository setting.) Skipping this is safe to recover from: the issue
lands in `flow/blocked-build` with the pushed branch intact, and adding
`flow` after flipping the setting resumes by opening the PR.

## 2. Add an agent credential

In the consumer repository (or the owning org), add a secret for the coding
agent you want to run — the workflows detect the agent from the secrets
that are provided:

| Secret | Agent that runs |
|--------|-----------------|
| `ANTHROPIC_API_KEY` (Anthropic API key) or `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, for Pro/Max subscriptions) | **Claude Code** via [anthropics/claude-code-action](https://github.com/anthropics/claude-code-action) |
| `OPENAI_API_KEY` | **Codex** via [openai/codex-action](https://github.com/openai/codex-action) |
| `GEMINI_API_KEY` | **Gemini CLI** via [google-github-actions/run-gemini-cli](https://github.com/google-github-actions/run-gemini-cli) |

One secret is enough. When secrets for several agents are present,
auto-detection prefers claude, then codex, then gemini; pass
`with: { agent: codex }` (or `gemini`, `claude`) to `shape` and `build` to
pick one explicitly.

Optional: `GF_BOT_TOKEN` — a PAT (contents: write, pull-requests: write)
used for pushing and creating PRs. Without it the default `GITHUB_TOKEN` is
used, which works but **does not trigger the repository's own CI on the
PRs the Crafter opens** (GitHub suppresses workflow runs for events created
with `GITHUB_TOKEN`). Add this when you want CI results on Crafter PRs.

## 3. Create the labels

```bash
bash scripts/setup-labels.sh <owner>/<repo>
```

(From a checkout of this repository, with `gh` authenticated.) This creates
the public `flow` trigger label and the `flow/*` state labels. The state
labels also self-heal — workflows create them on demand — but `flow` must
exist so humans can add it.

**Check for collisions first**: run `gh label list` on the target
repository. The script updates existing labels in place (`--force`), so a
pre-existing label named `flow` (or any `flow/*` label) would be
repurposed — and every add of that label would trigger a run. If the name
is taken, pick another trigger label:

```bash
bash scripts/setup-labels.sh <owner>/<repo> run-ai
```

and pass the same name to the workflows (see `trigger_label` below). The
trigger label must not start with `flow/` — that prefix is reserved for
the automation-owned state labels, and the workflows reject such a name.

## 4. Add the wrapper workflow

One file: `.github/workflows/issue-driven-flow.yml`

```yaml
name: issue-driven-flow

on:
  issues:
    types: [labeled, closed]
  pull_request:
    types: [labeled, closed, reopened]
  pull_request_review:
    types: [submitted]

jobs:
  shape:
    if: github.event_name == 'issues' && github.event.label.name == 'flow'
    uses: 4moda/issue-driven-flow/.github/workflows/shape.yml@v2
    permissions:
      contents: read
      issues: write
    secrets:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}
      gemini_api_key: ${{ secrets.GEMINI_API_KEY }}

  build:
    # fires for `flow` on an issue AND for `flow` on a flow/issue-N pull request
    if: github.event.label.name == 'flow'
    uses: 4moda/issue-driven-flow/.github/workflows/build.yml@v2
    permissions:
      contents: write
      issues: write
      pull-requests: write
    secrets:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}
      gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
      bot_token: ${{ secrets.GF_BOT_TOKEN }}

  sync-pr:
    if: >-
      github.event_name == 'pull_request_review' ||
      (github.event_name == 'pull_request' && github.event.action != 'labeled') ||
      (github.event_name == 'issues' && github.event.action == 'closed')
    uses: 4moda/issue-driven-flow/.github/workflows/sync-pr.yml@v2
    permissions:
      issues: write
      pull-requests: read
```

Both `shape` and `build` receive every `flow` trigger; a shared, tested
routing table (`scripts/gf.py`) guarantees exactly one of them acts, so the
double wiring is safe.

Versioning: `@v2` is a moving major tag — it always points at the latest
compatible release, and the workflows' internal action references use the
same tag, so everything stays in lockstep. Pin an exact release (e.g.
`@v2.0.0`) if you want to control upgrades yourself, or `@main` to track
unreleased changes. (`@v1`, frozen at v1.3.0, is the older line whose
trigger label was `ai`.)

To rename the trigger label (e.g. because `flow` is taken), pass
`with: { trigger_label: run-ai }` to `shape`, `build`, and `sync-pr`, and
change the two `github.event.label.name == 'flow'` conditions in the
wrapper to match. The name must not start with `flow/`.

To override the model, pass `with: { model: claude-opus-4-8 }` (or another
model id) to `shape` and `build`. Omitting it uses the selected agent's
default, and the id must belong to the agent that will run: Claude ids are
listed in the
[Anthropic models overview](https://docs.claude.com/en/docs/about-claude/models)
(e.g. `claude-opus-4-8`, `claude-sonnet-5`), Codex and Gemini ids in the
respective vendor docs.

Web research: by default the agents get web search/fetch tools so the
Composer can verify external facts (APIs, library capabilities) instead of
blocking on them, and the Crafter can consult documentation. Pass
`with: { web_research: false }` to `shape` and `build` to keep agent runs
offline apart from the model API. Per agent this maps to Claude's
`WebSearch`/`WebFetch` tools, Codex's `web_search`, and Gemini's
`web_fetch`/`google_web_search`.

Runaway guards: every agent run is bounded twice — by agent turns
(tunable via `with: { max_turns: N }`; defaults 50 for the Composer, 150
for the Crafter) and by wall-clock job timeouts (30 min shape, 90 min
build). The turn cap is enforced for claude and gemini; codex has no such
setting, so only the timeout bounds it. Exceeding either fails the run,
which lands the issue in the matching `flow/blocked-*` state with a
run-log link; add `flow` to retry. Lower `max_turns` to cap API spend per
run.

## First run

1. Open an issue describing what you want, in your own words.
2. Add the `flow` label. The Composer rewrites the issue into the shaped
   template and the issue moves to `flow/awaiting-approval`.
3. Review the shaped issue. Edit freely. When you agree, tick
   **ready for implementation** and add `flow` again.
4. The Crafter pushes branch `flow/issue-<n>` and opens a PR that closes the
   issue. Review it.
   - Merge when satisfied — the issue closes and is marked `flow/done`.
   - Or request changes: leave a PR review (or comments) describing what to
     change, then **add `flow` to the PR itself** — the Crafter reworks the
     same branch and PR with your feedback in context. Adding `flow` to the
     issue instead is equivalent.

If a run gets blocked, the issue gets a `flow/blocked-*` label and a comment
listing exactly what is missing. Answer in the issue and add `flow` to
resume.

If an issue is too large for one PR, the Composer splits it: sub-issues are
created already shaped (`flow/awaiting-approval`), the original becomes a
`flow/split` tracking issue with a checklist, and you approve and trigger
each sub-issue individually. Once every sub-issue has closed, `sync-pr.yml`
closes the tracking issue and marks it `flow/done` automatically — no human
step needed to notice the split finished.

## What humans manage

Only the `flow` label and the `ready for implementation` checkbox. All
`flow/*` labels are automation-owned — never add or remove them by hand
(if labels were tampered with, the next `flow` run explains how to recover).

## Notes and limits

- The Crafter cannot modify `.github/workflows/` in the consumer repository
  unless `GF_BOT_TOKEN` has the `workflow` scope; the default token rejects
  such pushes.
- Runs are serialized per issue (`concurrency` group), so adding `flow`
  during a run queues an acknowledge rather than racing.
- Minimum permissions are declared per job in the wrapper above; nothing in
  the flow needs more.
- The `flow/*` label namespace and `flow/issue-*` branch namespace are
  reserved for issue-driven-flow — don't create your own labels or branches with
  those prefixes in a consumer repository.
- The build run does **not** wait for the PR's own CI: merge is a human
  decision, so gate it with branch protection / required status checks on
  the PR instead. (The Crafter already runs the repository's tests inside
  its own run and reports the results in the PR text.) Remember that
  without `GF_BOT_TOKEN`, CI does not fire on Crafter PRs at all.
- If the consumer repository has a `PULL_REQUEST_TEMPLATE.md` (in
  `.github/`, the root, or `docs/`), the Crafter fills it in as the PR
  body. GitHub itself only applies PR templates to web-UI PRs, so
  issue-driven-flow replicates that for its API-created PRs; the closing
  keyword (`Closes #N`) and attribution footer are appended automatically
  either way.
- Each agent role may leave handoff notes on the issue as one auto-managed
  comment per role ("… handoff notes …"). They carry the agent's own
  memory (facts checked, decisions, open questions) into its next run on
  the same issue. Humans may edit or delete them freely; the current
  issue body and newer human comments always take precedence.
- Ordinary issue/PR conversation comments never trigger anything by
  themselves — runs start only from the `flow` label (plus PR
  close/reopen/review-submit and issue-closed events for the mechanical
  sync).
- Security model — whose tokens are used, what leaves GitHub, why the
  agents can't push or merge: see the
  [README](../README.md#security-model).
