"""Unit tests for scripts/apm.py manifest-driven skill placement."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import apm  # noqa: E402


def make_skill(root: Path, name: str = "github-flow") -> Path:
    skill_dir = root / name
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    (skill_dir / "references" / "composer.md").write_text("composer contract\n")
    return skill_dir


def write_manifest(manifest_dir: Path, targets: list[dict], name="github-flow"):
    manifest = {
        "skills": [
            {"name": name, "source": name, "targets": targets},
        ]
    }
    path = manifest_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


class PlaceTest(unittest.TestCase):
    def test_places_skill_at_declared_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            target_root.mkdir()

            placed = apm.place(manifest, target_root, tool="claude-code")

            dest = target_root / ".claude/skills/github-flow"
            self.assertEqual(placed, [dest])
            self.assertTrue((dest / "SKILL.md").is_file())
            self.assertEqual(
                (dest / "references" / "composer.md").read_text(),
                "composer contract\n",
            )

    def test_second_target_tool_needs_no_code_change(self):
        # proves the placement logic is generic over N targets: adding a
        # second tool entry to the manifest (a fixture, here) reaches its
        # own destination without touching apm.py
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root,
                [
                    {"tool": "claude-code", "path": ".claude/skills/github-flow"},
                    {"tool": "some-other-tool", "path": ".other/skills/github-flow"},
                ],
            )
            target_root = root / "target"
            target_root.mkdir()

            apm.place(manifest, target_root, tool="some-other-tool")

            self.assertTrue(
                (target_root / ".other/skills/github-flow/SKILL.md").is_file()
            )
            self.assertFalse((target_root / ".claude").exists())

    def test_no_tool_filter_places_every_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root,
                [
                    {"tool": "claude-code", "path": ".claude/skills/github-flow"},
                    {"tool": "some-other-tool", "path": ".other/skills/github-flow"},
                ],
            )
            target_root = root / "target"
            target_root.mkdir()

            placed = apm.place(manifest, target_root, tool=None)

            self.assertEqual(len(placed), 2)
            self.assertTrue((target_root / ".claude/skills/github-flow").is_dir())
            self.assertTrue((target_root / ".other/skills/github-flow").is_dir())

    def test_overwrites_stale_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            dest = target_root / ".claude/skills/github-flow"
            dest.mkdir(parents=True)
            (dest / "stale.md").write_text("leftover from a previous run\n")

            apm.place(manifest, target_root, tool="claude-code")

            self.assertFalse((dest / "stale.md").exists())
            self.assertTrue((dest / "SKILL.md").is_file())

    def test_missing_source_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            with self.assertRaises(apm.ManifestError):
                apm.place(manifest, root / "target", tool="claude-code")

    def test_source_missing_skill_md_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "github-flow").mkdir()
            (root / "github-flow" / "notes.md").write_text("not a skill\n")
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            with self.assertRaises(apm.ManifestError) as ctx:
                apm.place(manifest, root / "target", tool="claude-code")
            self.assertIn("SKILL.md", str(ctx.exception))

    def test_manifest_without_skills_array_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "manifest.json"
            path.write_text(json.dumps({"not-skills": []}))
            with self.assertRaises(apm.ManifestError):
                apm.place(path, root / "target", tool="claude-code")

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "manifest.json"
            path.write_text("{not json")
            with self.assertRaises(apm.ManifestError):
                apm.place(path, root / "target", tool="claude-code")

    def test_target_entry_missing_fields_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(root, [{"tool": "claude-code"}])  # no path
            with self.assertRaises(apm.ManifestError):
                apm.place(manifest, root / "target", tool="claude-code")

    def test_git_exclude_written_and_status_stays_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            target_root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=target_root, check=True)

            apm.place(manifest, target_root, tool="claude-code")

            exclude = (target_root / ".git" / "info" / "exclude").read_text()
            self.assertIn(".claude/skills/github-flow/", exclude)

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_root,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(status.stdout.strip(), "")

    def test_git_exclude_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            target_root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=target_root, check=True)

            apm.place(manifest, target_root, tool="claude-code")
            apm.place(manifest, target_root, tool="claude-code")

            exclude = (target_root / ".git" / "info" / "exclude").read_text()
            self.assertEqual(exclude.count(".claude/skills/github-flow/"), 1)

    def test_non_git_target_root_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            target_root.mkdir()

            placed = apm.place(manifest, target_root, tool="claude-code")
            self.assertTrue(placed[0].is_dir())


class CliTest(unittest.TestCase):
    def run_cli(self, argv):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = apm.main(argv)
        return code, stdout.getvalue()

    def test_place_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_skill(root)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            target_root = root / "target"
            target_root.mkdir()

            code, out = self.run_cli(
                [
                    "place",
                    "--manifest",
                    str(manifest),
                    "--target-root",
                    str(target_root),
                    "--tool",
                    "claude-code",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn(".claude/skills/github-flow", out)
            self.assertTrue(
                (target_root / ".claude/skills/github-flow/SKILL.md").is_file()
            )

    def test_place_reports_error_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root, [{"tool": "claude-code", "path": ".claude/skills/github-flow"}]
            )
            code, _ = self.run_cli(
                ["place", "--manifest", str(manifest), "--tool", "claude-code"]
            )
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
