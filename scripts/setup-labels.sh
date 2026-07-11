#!/usr/bin/env bash
# setup-labels.sh <owner/repo>
#
# Create the labels github-flow needs in a consumer repository. Idempotent:
# existing labels are updated in place (--force). Requires an authenticated
# gh CLI with access to the target repository.
set -euo pipefail

repo="${1:?usage: setup-labels.sh <owner/repo>}"

create() {
  gh label create "$1" --repo "$repo" --color "$2" --description "$3" --force
}

create "ai" "6f42c1" \
  "Run the next github-flow step on this issue"

create "flow/shaping" "fbca04" \
  "github-flow: Composer is shaping this issue (automation-managed)"
create "flow/awaiting-approval" "1d76db" \
  "github-flow: shaped, waiting for human approval (automation-managed)"
create "flow/building" "a2eeef" \
  "github-flow: Crafter is implementing (automation-managed)"
create "flow/pr-open" "0052cc" \
  "github-flow: a PR is open for this issue (automation-managed)"
create "flow/blocked-shape" "d93f0b" \
  "github-flow: shaping blocked, needs human input (automation-managed)"
create "flow/blocked-build" "b60205" \
  "github-flow: implementation blocked, needs human input (automation-managed)"
create "flow/done" "0e8a16" \
  "github-flow: PR merged, flow complete (automation-managed)"
create "flow/split" "5319e7" \
  "github-flow: split into sub-issues, tracking only (automation-managed)"

echo "github-flow labels are set up in ${repo}."
