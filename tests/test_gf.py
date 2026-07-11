"""Unit tests for scripts/gf.py routing and parsing logic."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gf  # noqa: E402


def issue(labels=(), body="", state="open", pull_request=False):
    payload = {
        "number": 1,
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

    def test_route_pr_trigger(self):
        code, out = self.run_cli(
            ["route", "--workflow", "build", "--trigger", "pr"],
            issue(labels=["flow/pr-open"]),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "build")


if __name__ == "__main__":
    unittest.main()
