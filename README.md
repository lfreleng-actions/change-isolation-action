<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 🔒 Change Isolation Action

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/change-isolation-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
<!-- prettier-ignore-end -->

Verify that a change keeps modifications **isolated** to a set of paths.

Given a list of gitignore-style patterns describing the "in-scope" paths, the
action takes the files changed between a base reference and `HEAD` (or a
list you supply) and applies a single rule:

> If **any** changed file matches the in-scope patterns, then **every** changed
> file must match them. If **no** changed file matches, the check is a no-op
> and passes.

This makes it easy to enforce that, for example, an `INFO.yaml` change is not
combined with anything else, or that CI/CD changes under `.github/` are
ring-fenced from code changes. Running the action twice with different `paths`
makes those two scopes mutually exclusive.

## Usage Example

<!-- markdownlint-disable MD046 -->

```yaml
steps:
  - name: "Checkout"
    uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
    with:
      fetch-depth: 2 # parent commit required to diff a single-commit change

  - name: "Isolate INFO.yaml changes"
    uses: lfreleng-actions/change-isolation-action@main
    with:
      paths: "/INFO.yaml"

  - name: "Isolate .github (CI/CD) changes"
    uses: lfreleng-actions/change-isolation-action@main
    with:
      paths: ".github/**"
```

<!-- markdownlint-enable MD046 -->

> [!NOTE]
> The examples reference `@main` for readability, matching the convention
> used across the `lfreleng-actions` estate. In production workflows, pin
> `uses:` to the commit SHA of a tagged release (keeping the version in a
> trailing comment), as done for `actions/checkout` above.

### Supplying the changed files

When an earlier job has already computed the changed file list, pass it
through `changed-files`. A non-empty list replaces git entirely: the action
runs no git command, needs no checkout and ignores `base-ref`. Jobs that check
one pattern set each can then skip a checkout of their own.

<!-- markdownlint-disable MD046 -->

```yaml
steps:
  - name: "Isolate INFO.yaml changes"
    uses: lfreleng-actions/change-isolation-action@main
    with:
      paths: "/INFO.yaml"
      changed-files: ${{ needs.plan.outputs.changed-files }}
```

<!-- markdownlint-enable MD046 -->

The action splits the list on newlines, trims surrounding whitespace and drops
blank lines. It takes each entry as-is, in the form `git diff` lists paths,
and fails the step on an entry containing a control character. The action
cannot tell an empty value from an absent one, so a change with no files at
all falls back to git; skip the step in that case.

## Pattern Semantics

Patterns use gitignore semantics. The action matches them with the Python
standard library, and its tests hold the results identical to the
`gitignore` pattern of [`pathspec`](https://pypi.org/project/pathspec/),
which earlier releases used:

| Pattern      | Matches                                               |
| ------------ | ----------------------------------------------------- |
| `/INFO.yaml` | A top-level `INFO.yaml` (leading `/` anchors to root) |
| `INFO.yaml`  | An `INFO.yaml` at any depth                           |
| `.github/**` | Everything beneath the `.github` directory            |
| `a/**/b`     | `a/b`, `a/x/b`, `a/x/y/b` and so on                   |
| `*.py`       | Any `.py` file; `*`, `?` and `[...]` stay in one name |
| `build/`     | Everything beneath any `build` directory              |
| `!.github/x` | Negation; excludes `.github/x` from the in-scope set  |

When more than one pattern matches a path, the last one wins, so a later
pattern can re-include a path an earlier `!` pattern excluded. A backslash
escapes the next character (`\#notes`, `\!bang`), and the action skips
lines starting with `#`. A pattern it cannot compile, such as one ending in
a lone backslash, fails the step with an error naming it.

## Inputs

<!-- markdownlint-disable MD013 -->

| Name                | Required | Default  | Description                                                                                 |
| ------------------- | -------- | -------- | ------------------------------------------------------------------------------------------- |
| `paths`             | True     |          | Newline-separated gitignore-style patterns describing the in-scope paths that must isolate. |
| `base-ref`          | False    | `HEAD~1` | Git reference to diff `HEAD` against. Must exist in local history (increase `fetch-depth`). |
| `changed-files`     | False    | `""`     | Newline-separated paths to check instead of a `git diff`. When set, ignores `base-ref`.     |
| `fail-on-violation` | False    | `"true"` | When `true`, fail on a violation. When `false`, report a warning without failing the step.  |

<!-- markdownlint-enable MD013 -->

## Outputs

<!-- markdownlint-disable MD013 -->

| Name              | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `isolated`        | `true` when the change stays isolated, otherwise `false`.          |
| `triggered`       | `true` if at least one changed file matched the in-scope patterns. |
| `scanned`         | Deprecated alias for `triggered`; use `triggered` instead.         |
| `violating-files` | Newline-separated list of out-of-scope files that break isolation. |
| `changed-files`   | Newline-separated list of the changed files the action evaluated.  |

<!-- markdownlint-enable MD013 -->

## Implementation Details

The matching logic lives in `change_isolation.py` (a standalone
[PEP 723](https://peps.python.org/pep-0723/) script run with
`uv run --locked --script`, pinned by the committed
`change_isolation.py.lock` for reproducible, hash-verified
dependencies). Pure functions handle pattern matching, and a `pytest`
suite exercises them; a thin `main()` reads the `INPUT_*` environment
variables, takes the changed file list from the `changed-files` input or
from a `git diff` against the base ref, and writes outputs, a
`$GITHUB_STEP_SUMMARY` block and `::error::`/`::warning::` annotations.

A shallow checkout that lacks the base commit fails with an actionable message
rather than producing a misleading result; supply a deeper `fetch-depth`
(`2` is enough for a single-commit Gerrit change) or an explicit `base-ref`.

## Notes

This action is primarily consumed by the Linux Foundation reusable workflows
that check Gerrit changes mirrored to GitHub, replacing bespoke shell-based
isolation checks with a single, testable implementation.
