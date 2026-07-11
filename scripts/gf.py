#!/usr/bin/env python3
"""Decision logic for github-flow workflows.

Reads GitHub issue JSON on stdin and prints a result. Keeping the logic
here (stdlib only, no network) makes it unit testable; fetching data and
mutating GitHub state stay in the workflows and composite actions.

Subcommands:
  state   Print the issue's flow/* state label, or "none". Exits 2 when the
          issue carries more than one state label.
  ready   Print "true"/"false" for the "ready for implementation" checkbox.
  route   Print what a workflow should do when the trigger label is added
          to the issue. --workflow is "shape" or "build"; --trigger-label
          names the public trigger label (default "flow"); --format github
          prints GITHUB_OUTPUT-style key=value lines.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

STATE_PREFIX = "flow/"
NO_STATE = "none"

SHAPING = "flow/shaping"
AWAITING_APPROVAL = "flow/awaiting-approval"
BUILDING = "flow/building"
PR_OPEN = "flow/pr-open"
BLOCKED_SHAPE = "flow/blocked-shape"
BLOCKED_BUILD = "flow/blocked-build"
DONE = "flow/done"
SPLIT = "flow/split"

KNOWN_STATES = {
    SHAPING,
    AWAITING_APPROVAL,
    BUILDING,
    PR_OPEN,
    BLOCKED_SHAPE,
    BLOCKED_BUILD,
    DONE,
    SPLIT,
}

READY_RE = re.compile(
    r"^\s*[-*]\s+\[[xX]\]\s.*ready for implementation",
    re.IGNORECASE | re.MULTILINE,
)


class MultipleStateLabelsError(Exception):
    def __init__(self, labels: list[str]):
        super().__init__(f"multiple flow/* state labels: {', '.join(labels)}")
        self.labels = labels


def state_labels(issue: dict) -> list[str]:
    names = []
    for label in issue.get("labels") or []:
        name = label if isinstance(label, str) else (label.get("name") or "")
        if name.startswith(STATE_PREFIX):
            names.append(name)
    return names


def detect_state(issue: dict) -> str:
    labels = state_labels(issue)
    if len(labels) > 1:
        raise MultipleStateLabelsError(labels)
    return labels[0] if labels else NO_STATE


def is_ready(issue: dict) -> bool:
    return bool(READY_RE.search(issue.get("body") or ""))


def route(
    issue: dict, workflow: str, trigger: str = "issue", trigger_label: str = "flow"
) -> dict:
    """Decide what `workflow` (shape or build) should do with `issue`.

    `trigger` is "issue" when the trigger label was added to the issue
    itself, or "pr" when it was added to the issue's pull request (the
    rework shortcut: review the PR, then label the PR). PR triggers only
    reach build.yml, so for them build.yml owns the acknowledge role; for
    issue triggers shape.yml owns it. Either way, every trigger is answered
    exactly once: for any issue, at most one workflow returns a non-"skip"
    action. `trigger_label` is only interpolated into human-facing notes.

    Returns {"action", "state", "note", "first_run"}.
    """

    def result(action: str, state: str, note: str = "", first_run: bool = False) -> dict:
        return {
            "action": action,
            "state": state,
            # notes are emitted as single GITHUB_OUTPUT lines
            "note": " ".join(note.split()),
            "first_run": first_run,
        }

    ack = "acknowledge" if (workflow == "shape" or trigger == "pr") else "skip"

    # a payload that is itself a pull request is not part of the flow
    if "pull_request" in issue:
        return result("skip", NO_STATE)

    try:
        state = detect_state(issue)
    except MultipleStateLabelsError as err:
        return result(
            ack,
            "invalid",
            "This issue carries multiple `flow/*` state labels "
            f"({', '.join(err.labels)}). Remove the extras so at most one "
            f"remains, then add `{trigger_label}` again.",
        )

    if issue.get("state") == "closed":
        return result(
            ack, state, "This issue is closed; github-flow only runs on open issues."
        )

    if state in (NO_STATE, BLOCKED_SHAPE):
        if workflow == "shape":
            return result("shape", state)
        if trigger == "pr":
            return result(
                ack,
                state,
                f"The linked issue is not in a buildable state (`{state}`). "
                "Shape and approve it on the issue itself first.",
            )
        return result("skip", state)

    if state == AWAITING_APPROVAL:
        if is_ready(issue):
            return result("build" if workflow == "build" else "skip", state, first_run=True)
        # unchecked box = not approved yet: send the issue back to the Composer
        if workflow == "shape":
            return result("shape", state)
        if trigger == "pr":
            return result(
                ack,
                state,
                'The linked issue\'s "ready for implementation" checkbox is '
                "not ticked. Approve it on the issue itself first.",
            )
        return result("skip", state)

    if state in (BLOCKED_BUILD, PR_OPEN):
        return result("build" if workflow == "build" else "skip", state)

    if state in (SHAPING, BUILDING):
        return result(
            ack,
            state,
            "A github-flow run is already in progress for this issue. "
            f"Wait for it to finish, then add `{trigger_label}` again if needed.",
        )

    if state == DONE:
        return result(
            ack,
            state,
            "This issue already completed the flow (`flow/done`). "
            "Open a new issue for follow-up work.",
        )

    if state == SPLIT:
        return result(
            ack,
            state,
            "This issue was split into sub-issues; run the flow on those "
            "instead (see the Sub-issues section).",
        )

    return result(
        ack,
        state,
        f"Unrecognized state label `{state}`. Remove it, then add "
        f"`{trigger_label}` again.",
    )


def _print_github_output(res: dict, out) -> None:
    out.write(f"action={res['action']}\n")
    out.write(f"state={res['state']}\n")
    out.write(f"note={res['note']}\n")
    out.write("first-run={}\n".format("true" if res["first_run"] else "false"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gf.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    sub.add_parser("ready")
    route_p = sub.add_parser("route")
    route_p.add_argument("--workflow", choices=["shape", "build"], required=True)
    route_p.add_argument("--trigger", choices=["issue", "pr"], default="issue")
    route_p.add_argument("--trigger-label", default="flow")
    route_p.add_argument("--format", choices=["json", "github"], default="json")
    args = parser.parse_args(argv)

    issue = json.load(sys.stdin)

    if args.command == "state":
        try:
            print(detect_state(issue))
        except MultipleStateLabelsError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2
    elif args.command == "ready":
        print("true" if is_ready(issue) else "false")
    elif args.command == "route":
        res = route(issue, args.workflow, args.trigger, args.trigger_label)
        if args.format == "github":
            _print_github_output(res, sys.stdout)
        else:
            json.dump(res, sys.stdout)
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
