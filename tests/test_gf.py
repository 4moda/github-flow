"""Unit tests for scripts/gf.py routing and parsing logic."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gf  # noqa: E402


def issue(labels=(), body="", state="open", pull_request=False, number=1):
    payload = {
        "number": number,
        "state": state,
        "body": body,
        "labels": [{"name": name} for name in labels],
    }
    if pull_request:
        payload["pull_request"] = {"url": "https://example.invalid"}
    return payload


READY_BODY = "## AI Ready\n- [x] ready for implementation\n"
UNREADY_BODY = "## AI Ready\n- [ ] ready for implementation\n"


class DetectStateTest(unittest.TestCase):
    def test_no_labels_is_none(self):
        self.assertEqual(gf.detect_state(issue()), "none")

    def test_non_state_labels_ignored(self):
        self.assertEqual(gf.detect_state(issue(labels=["bug", "flow"])), "none")

    def test_single_state_label(self):
        self.assertEqual(
            gf.detect_state(issue(labels=["bug", "flow/pr-open"])), "flow/pr-open"
        )

    def test_multiple_state_labels_raise(self):
        with self.assertRaises(gf.MultipleStateLabelsError):
            gf.detect_state(issue(labels=["flow/shaping", "flow/building"]))

    def test_string_labels_accepted(self):
        payload = issue()
        payload["labels"] = ["flow/done"]
        self.assertEqual(gf.detect_state(payload), "flow/done")


class IsReadyTest(unittest.TestCase):
    def test_checked_dash(self):
        self.assertTrue(gf.is_ready(issue(body="- [x] ready for implementation")))

    def test_checked_uppercase(self):
        self.assertTrue(gf.is_ready(issue(body="- [X] Ready For Implementation")))

    def test_checked_asterisk_indented(self):
        self.assertTrue(gf.is_ready(issue(body="  * [x] ready for implementation")))

    def test_unchecked(self):
        self.assertFalse(gf.is_ready(issue(body="- [ ] ready for implementation")))

    def test_other_checked_boxes_dont_count(self):
        body = "- [x] add logging\n- [ ] ready for implementation"
        self.assertFalse(gf.is_ready(issue(body=body)))

    def test_missing_body(self):
        payload = issue()
        payload["body"] = None
        self.assertFalse(gf.is_ready(payload))

    def test_mid_line_mention_not_counted(self):
        self.assertFalse(gf.is_ready(issue(body="see [x] ready for implementation")))


class RouteTest(unittest.TestCase):
    """Every state must be handled by exactly one workflow (or acknowledged)."""

    def route(self, payload):
        return gf.route(payload, "shape"), gf.route(payload, "build")

    def assert_exclusive(self, payload):
        shape, build = self.route(payload)
        acting = [r for r in (shape, build) if r["action"] != "skip"]
        self.assertLessEqual(len(acting), 1, (shape, build))
        return shape, build

    def test_new_issue_routes_to_shape(self):
        shape, build = self.assert_exclusive(issue())
        self.assertEqual(shape["action"], "shape")
        self.assertEqual(build["action"], "skip")

    def test_blocked_shape_routes_to_shape(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/blocked-shape"]))
        self.assertEqual(shape["action"], "shape")
        self.assertEqual(build["action"], "skip")

    def test_awaiting_approval_ready_routes_to_build(self):
        shape, build = self.assert_exclusive(
            issue(labels=["flow/awaiting-approval"], body=READY_BODY)
        )
        self.assertEqual(shape["action"], "skip")
        self.assertEqual(build["action"], "build")
        self.assertTrue(build["first_run"])

    def test_awaiting_approval_unready_reshapes(self):
        shape, build = self.assert_exclusive(
            issue(labels=["flow/awaiting-approval"], body=UNREADY_BODY)
        )
        self.assertEqual(shape["action"], "shape")
        self.assertEqual(build["action"], "skip")

    def test_blocked_build_routes_to_build(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/blocked-build"]))
        self.assertEqual(shape["action"], "skip")
        self.assertEqual(build["action"], "build")
        self.assertFalse(build["first_run"])

    def test_pr_open_routes_to_build(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/pr-open"]))
        self.assertEqual(build["action"], "build")

    def test_in_progress_states_acknowledged_by_shape(self):
        for label in ("flow/shaping", "flow/building"):
            with self.subTest(label=label):
                shape, build = self.assert_exclusive(issue(labels=[label]))
                self.assertEqual(shape["action"], "acknowledge")
                self.assertIn("in progress", shape["note"])
                self.assertEqual(build["action"], "skip")

    def test_done_acknowledged_by_shape(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/done"]))
        self.assertEqual(shape["action"], "acknowledge")
        self.assertEqual(build["action"], "skip")

    def test_unknown_state_acknowledged_by_shape(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/bogus"]))
        self.assertEqual(shape["action"], "acknowledge")
        self.assertIn("flow/bogus", shape["note"])
        self.assertEqual(build["action"], "skip")

    def test_multiple_labels_acknowledged_by_shape(self):
        payload = issue(labels=["flow/shaping", "flow/pr-open"])
        shape, build = self.assert_exclusive(payload)
        self.assertEqual(shape["action"], "acknowledge")
        self.assertEqual(shape["state"], "invalid")
        self.assertEqual(build["action"], "skip")

    def test_closed_issue_acknowledged_by_shape(self):
        shape, build = self.assert_exclusive(issue(state="closed"))
        self.assertEqual(shape["action"], "acknowledge")
        self.assertIn("closed", shape["note"])
        self.assertEqual(build["action"], "skip")

    def test_closed_issue_with_buildable_state_not_built(self):
        payload = issue(labels=["flow/pr-open"], state="closed")
        shape, build = self.assert_exclusive(payload)
        self.assertEqual(build["action"], "skip")
        self.assertEqual(shape["action"], "acknowledge")

    def test_pull_request_payload_skipped_by_both(self):
        payload = issue(pull_request=True)
        shape, build = self.route(payload)
        self.assertEqual(shape["action"], "skip")
        self.assertEqual(build["action"], "skip")

    def test_split_acknowledged_by_shape(self):
        shape, build = self.assert_exclusive(issue(labels=["flow/split"]))
        self.assertEqual(shape["action"], "acknowledge")
        self.assertIn("sub-issues", shape["note"])
        self.assertEqual(build["action"], "skip")

    def test_notes_are_single_line(self):
        for labels in ([], ["flow/shaping"], ["flow/done"], ["flow/a", "flow/b"]):
            for workflow in ("shape", "build"):
                res = gf.route(issue(labels=labels), workflow)
                self.assertNotIn("\n", res["note"])

    def test_notes_name_the_default_trigger_label(self):
        res = gf.route(issue(labels=["flow/shaping"]), "shape")
        self.assertIn("`flow`", res["note"])

    def test_notes_name_a_custom_trigger_label(self):
        for labels in (["flow/shaping"], ["flow/bogus"], ["flow/a", "flow/b"]):
            with self.subTest(labels=labels):
                res = gf.route(issue(labels=labels), "shape", trigger_label="run-ai")
                self.assertIn("`run-ai`", res["note"])
                self.assertNotIn("`flow`", res["note"])

    def test_bare_trigger_label_is_not_a_state(self):
        # the default trigger label `flow` must not match the `flow/` state
        # namespace prefix
        self.assertEqual(gf.detect_state(issue(labels=["flow"])), "none")
        res = gf.route(issue(labels=["flow"]), "shape")
        self.assertEqual(res["action"], "shape")


class OpenBlockedByRouteTest(unittest.TestCase):
    """An open 'blocked by' dependency gates build routing only; shape
    routing (issue shaping) is never affected by dependency state."""

    BUILDABLE_LABELS = (
        ["flow/awaiting-approval"],  # with READY_BODY below
        ["flow/blocked-build"],
        ["flow/pr-open"],
    )

    def buildable_issue(self, labels):
        body = READY_BODY if labels == ["flow/awaiting-approval"] else ""
        return issue(labels=labels, body=body)

    def test_build_blocked_by_open_dependency(self):
        for labels in self.BUILDABLE_LABELS:
            with self.subTest(labels=labels):
                payload = self.buildable_issue(labels)
                res = gf.route(payload, "build", open_blocked_by=[12, 34])
                self.assertEqual(res["action"], "acknowledge")
                self.assertIn("#12", res["note"])
                self.assertIn("#34", res["note"])
                self.assertFalse(res["first_run"])

    def test_build_runs_when_all_dependencies_closed_or_none(self):
        for labels in self.BUILDABLE_LABELS:
            for open_blocked_by in (None, []):
                with self.subTest(labels=labels, open_blocked_by=open_blocked_by):
                    payload = self.buildable_issue(labels)
                    res = gf.route(payload, "build", open_blocked_by=open_blocked_by)
                    self.assertEqual(res["action"], "build")

    def test_shape_routing_unaffected_by_open_dependency(self):
        # shapeable states: no state label, flow/blocked-shape, and
        # flow/awaiting-approval with the checkbox still unticked
        for payload in (
            issue(),
            issue(labels=["flow/blocked-shape"]),
            issue(labels=["flow/awaiting-approval"], body=UNREADY_BODY),
        ):
            with self.subTest(payload=payload):
                res = gf.route(payload, "shape", open_blocked_by=[12, 34])
                self.assertEqual(res["action"], "shape")

    def test_pr_trigger_build_also_gated(self):
        payload = issue(labels=["flow/pr-open"])
        res = gf.route(payload, "build", trigger="pr", open_blocked_by=[7])
        self.assertEqual(res["action"], "acknowledge")
        self.assertIn("#7", res["note"])

    def test_note_names_the_trigger_label(self):
        payload = self.buildable_issue(["flow/blocked-build"])
        res = gf.route(payload, "build", trigger_label="run-ai", open_blocked_by=[5])
        self.assertIn("`run-ai`", res["note"])


class PrTriggerRouteTest(unittest.TestCase):
    """The trigger label added to a flow/issue-N pull request routes only
    through build.yml, which then owns the acknowledge role."""

    def route_pr(self, payload):
        return gf.route(payload, "build", trigger="pr")

    def test_pr_open_builds(self):
        res = self.route_pr(issue(labels=["flow/pr-open"]))
        self.assertEqual(res["action"], "build")
        self.assertFalse(res["first_run"])

    def test_blocked_build_builds(self):
        res = self.route_pr(issue(labels=["flow/blocked-build"]))
        self.assertEqual(res["action"], "build")

    def test_awaiting_approval_ready_builds(self):
        res = self.route_pr(issue(labels=["flow/awaiting-approval"], body=READY_BODY))
        self.assertEqual(res["action"], "build")
        self.assertTrue(res["first_run"])

    def test_awaiting_approval_unready_acknowledged(self):
        res = self.route_pr(issue(labels=["flow/awaiting-approval"], body=UNREADY_BODY))
        self.assertEqual(res["action"], "acknowledge")
        self.assertIn("checkbox", res["note"])

    def test_unshaped_issue_acknowledged(self):
        res = self.route_pr(issue())
        self.assertEqual(res["action"], "acknowledge")
        self.assertIn("not in a buildable state", res["note"])

    def test_in_progress_acknowledged(self):
        for label in ("flow/shaping", "flow/building"):
            with self.subTest(label=label):
                res = self.route_pr(issue(labels=[label]))
                self.assertEqual(res["action"], "acknowledge")

    def test_done_and_split_acknowledged(self):
        for label in ("flow/done", "flow/split"):
            with self.subTest(label=label):
                res = self.route_pr(issue(labels=[label]))
                self.assertEqual(res["action"], "acknowledge")

    def test_closed_issue_acknowledged(self):
        res = self.route_pr(issue(labels=["flow/pr-open"], state="closed"))
        self.assertEqual(res["action"], "acknowledge")
        self.assertIn("closed", res["note"])

    def test_multiple_labels_acknowledged(self):
        res = self.route_pr(issue(labels=["flow/pr-open", "flow/building"]))
        self.assertEqual(res["action"], "acknowledge")
        self.assertEqual(res["state"], "invalid")


class SplitParentNumberTest(unittest.TestCase):
    def test_parent_present(self):
        self.assertEqual(gf.split_parent_number(issue(number=42)), 42)

    def test_parent_none(self):
        # GET .../issues/{n}/parent 404s when the issue has no parent
        self.assertIsNone(gf.split_parent_number(None))

    def test_parent_empty_dict(self):
        self.assertIsNone(gf.split_parent_number({}))


class SplitCompleteTest(unittest.TestCase):
    def test_all_siblings_closed(self):
        siblings = [issue(number=2, state="closed"), issue(number=3, state="closed")]
        self.assertTrue(gf.split_complete(siblings))

    def test_some_siblings_still_open(self):
        siblings = [issue(number=2, state="closed"), issue(number=3, state="open")]
        self.assertFalse(gf.split_complete(siblings))

    def test_no_sub_issues(self):
        self.assertFalse(gf.split_complete([]))


class CliTest(unittest.TestCase):
    def run_cli(self, argv, stdin_payload):
        stdin = io.StringIO(json.dumps(stdin_payload))
        stdout = io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            with redirect_stdout(stdout):
                code = gf.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, stdout.getvalue()

    def test_state_command(self):
        code, out = self.run_cli(["state"], issue(labels=["flow/pr-open"]))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "flow/pr-open")

    def test_state_command_multiple_labels_exit_2(self):
        code, _ = self.run_cli(["state"], issue(labels=["flow/shaping", "flow/done"]))
        self.assertEqual(code, 2)

    def test_ready_command(self):
        code, out = self.run_cli(["ready"], issue(body=READY_BODY))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "true")

    def test_route_json_format(self):
        code, out = self.run_cli(["route", "--workflow", "shape"], issue())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "shape")

    def test_route_github_format(self):
        code, out = self.run_cli(
            ["route", "--workflow", "build", "--format", "github"],
            issue(labels=["flow/awaiting-approval"], body=READY_BODY),
        )
        self.assertEqual(code, 0)
        lines = dict(line.split("=", 1) for line in out.strip().splitlines())
        self.assertEqual(lines["action"], "build")
        self.assertEqual(lines["first-run"], "true")

    def test_route_open_blocked_by(self):
        code, out = self.run_cli(
            ["route", "--workflow", "build", "--open-blocked-by", "12,34"],
            issue(labels=["flow/pr-open"]),
        )
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["action"], "acknowledge")
        self.assertIn("#12", res["note"])
        self.assertIn("#34", res["note"])

    def test_route_open_blocked_by_empty_string_is_no_gate(self):
        code, out = self.run_cli(
            ["route", "--workflow", "build", "--open-blocked-by", ""],
            issue(labels=["flow/pr-open"]),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "build")

    def test_route_pr_trigger(self):
        code, out = self.run_cli(
            ["route", "--workflow", "build", "--trigger", "pr"],
            issue(labels=["flow/pr-open"]),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "build")

    def test_split_parent_command(self):
        code, out = self.run_cli(["split-parent"], issue(number=5))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "5")

    def test_split_parent_command_no_parent(self):
        code, out = self.run_cli(["split-parent"], None)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_split_complete_command(self):
        siblings = [issue(number=2, state="closed"), issue(number=3, state="closed")]
        code, out = self.run_cli(["split-complete"], siblings)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "true")

    def test_split_complete_command_incomplete(self):
        siblings = [issue(number=2, state="closed"), issue(number=3, state="open")]
        code, out = self.run_cli(["split-complete"], siblings)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "false")


if __name__ == "__main__":
    unittest.main()
