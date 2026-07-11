# Shaped issue template

The Composer rewrites issue bodies into this exact shape. Section order and
headings are fixed; content is adapted to the issue.

```markdown
## Background

<Why this matters. Current behavior, relevant history, links.>

## Problem

<What is wrong or missing, concretely. Observable symptoms over opinions.>

## Proposed approach

<How to solve it, at design level. No code. Name the mechanism, not the
diff.>

## Acceptance criteria

- [ ] <Objectively checkable statement — a reviewer can answer yes/no>
- [ ] <...>

## Out of scope

- <Explicitly excluded work, so the Crafter does not drift. "None" is fine.>

## Dependencies

- <Issues, PRs, or decisions this waits on. "None" is fine.>

## Likely touched areas

- `path/to/file-or-dir` — <why it is involved>

## AI Ready

- [ ] ready for implementation

<details>
<summary>Original request</summary>

<The issue body exactly as the human wrote it, verbatim.>

</details>
```

## Rules

- The checkbox line must contain the literal text `ready for
  implementation` — `build.yml` detects approval by matching a checked
  checkbox against that phrase (case-insensitive, `-` or `*` bullets).
- The Composer always emits the checkbox **unticked**; only humans tick it.
- Humans may edit any section after shaping. Editing plus re-adding `ai`
  while the box is unticked re-runs the Composer on the edited body.
- The `Original request` block is written once, at first shaping, and then
  preserved verbatim by later re-shapes.
- Sub-issues created by a split use this same template; their
  `Original request` block may simply reference the parent issue.
