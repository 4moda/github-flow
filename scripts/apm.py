#!/usr/bin/env python3
"""Manifest-driven placement for github-flow's skill packages.

Reads a JSON manifest declaring skill sources and, per skill, the
destination directory for each supported "target" tool (e.g. Claude
Code's ``.claude/skills/<name>/``). Adding a destination for another
tool is a manifest edit — one more entry in a skill's ``targets`` list —
not a code change here.

Manifest shape:

    {
      "skills": [
        {
          "name": "github-flow",
          "source": "github-flow",
          "targets": [
            {"tool": "claude-code", "path": ".claude/skills/github-flow"}
          ]
        }
      ]
    }

``source`` is a path relative to the manifest's own directory. ``path``
in each target is relative to --target-root.

Subcommands:
  place   Copy each matching skill's source tree to its declared
          destination(s) under --target-root. When --target-root is
          inside a git working tree, each destination is recorded in
          .git/info/exclude so placed files never show up in `git
          status` or get swept into a commit by `git add -A`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


class ManifestError(Exception):
    pass


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except OSError as err:
        raise ManifestError(f"cannot read manifest {path}: {err}") from err
    except json.JSONDecodeError as err:
        raise ManifestError(f"manifest {path} is not valid JSON: {err}") from err
    if not isinstance(data.get("skills"), list):
        raise ManifestError(f"manifest {path} has no 'skills' array")
    return data


def resolve_targets(
    manifest: dict, tool: str | None
) -> list[tuple[str, Path, Path]]:
    """Return (skill_name, source_dir, dest_relative_path) for every
    target matching `tool` (every target, when `tool` is None)."""
    resolved = []
    for skill in manifest["skills"]:
        name = skill.get("name")
        source = skill.get("source")
        if not name or not source:
            raise ManifestError(f"skill entry missing 'name' or 'source': {skill}")
        for target in skill.get("targets", []):
            target_tool = target.get("tool")
            target_path = target.get("path")
            if not target_tool or not target_path:
                raise ManifestError(
                    f"target entry for skill {name!r} missing 'tool' or "
                    f"'path': {target}"
                )
            if tool is not None and target_tool != tool:
                continue
            resolved.append((name, Path(source), Path(target_path)))
    return resolved


def git_exclude(target_root: Path, relative_dest: Path) -> None:
    """Best-effort: record relative_dest in .git/info/exclude under
    target_root, so a placed skill never leaks into a commit. Does
    nothing when target_root isn't inside a git working tree.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=target_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = target_root / git_dir
    info_dir = git_dir / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    exclude_file = info_dir / "exclude"
    entry = str(relative_dest).rstrip("/") + "/"
    existing = (
        exclude_file.read_text().splitlines() if exclude_file.exists() else []
    )
    if entry not in existing:
        with exclude_file.open("a") as fh:
            fh.write(entry + "\n")


def place(manifest_path: Path, target_root: Path, tool: str | None) -> list[Path]:
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent
    placed = []
    for name, source_rel, dest_rel in resolve_targets(manifest, tool):
        source_dir = (manifest_dir / source_rel).resolve()
        if not source_dir.is_dir():
            raise ManifestError(f"skill {name!r} source not found: {source_dir}")
        if not (source_dir / "SKILL.md").is_file():
            raise ManifestError(
                f"skill {name!r} source has no SKILL.md: {source_dir}"
            )
        dest_dir = target_root / dest_rel
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, dest_dir)
        git_exclude(target_root, dest_rel)
        placed.append(dest_dir)
    return placed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apm.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    place_p = sub.add_parser("place")
    place_p.add_argument("--manifest", required=True, type=Path)
    place_p.add_argument("--target-root", default=Path("."), type=Path)
    place_p.add_argument("--tool", default=None)

    args = parser.parse_args(argv)

    if args.command == "place":
        try:
            placed = place(
                args.manifest.resolve(), args.target_root.resolve(), args.tool
            )
        except ManifestError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        for path in placed:
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
