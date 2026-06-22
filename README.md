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
action computes the files changed between a base reference and `HEAD` and
applies a single rule:

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

## Pattern Semantics

Patterns use gitignore semantics (via
[`pathspec`](https://pypi.org/project/pathspec/)):

| Pattern      | Matches                                               |
| ------------ | ----------------------------------------------------- |
| `/INFO.yaml` | A top-level `INFO.yaml` (leading `/` anchors to root) |
| `INFO.yaml`  | An `INFO.yaml` at any depth                           |
| `.github/**` | Everything beneath the `.github` directory            |
| `!.github/x` | Negation; excludes `.github/x` from the in-scope set  |

## Inputs

<!-- markdownlint-disable MD013 -->

| Name                | Required | Default  | Description                                                                                 |
| ------------------- | -------- | -------- | ------------------------------------------------------------------------------------------- |
| `paths`             | True     |          | Newline-separated gitignore-style patterns describing the in-scope paths that must isolate. |
| `base-ref`          | False    | `HEAD~1` | Git reference to diff `HEAD` against. Must exist in local history (increase `fetch-depth`). |
| `fail-on-violation` | False    | `"true"` | When `true`, fail on a violation. When `false`, report a warning without failing the step.  |

<!-- markdownlint-enable MD013 -->

## Outputs

<!-- markdownlint-disable MD013 -->

| Name              | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `isolated`        | `true` when the change stays isolated, otherwise `false`.          |
| `scanned`         | `true` if at least one changed file matched the in-scope patterns. |
| `violating-files` | Newline-separated list of out-of-scope files that break isolation. |

<!-- markdownlint-enable MD013 -->

## Implementation Details

The matching logic lives in `change_isolation.py` (a standalone
[PEP 723](https://peps.python.org/pep-0723/) script run with
`uv run --locked --script`, pinned by the committed
`change_isolation.py.lock` for reproducible, hash-verified
dependencies). Pure functions handle pattern matching, and a `pytest`
suite exercises them; a thin `main()` reads the `INPUT_*` environment
variables, collects the changed file list with a `git diff` against the base
ref, and writes outputs, a `$GITHUB_STEP_SUMMARY` block and
`::error::`/`::warning::` annotations.

A shallow checkout that lacks the base commit fails with an actionable message
rather than producing a misleading result; supply a deeper `fetch-depth`
(`2` is enough for a single-commit Gerrit change) or an explicit `base-ref`.

## Notes

This action is primarily consumed by the Linux Foundation reusable workflows
that check Gerrit changes mirrored to GitHub, replacing bespoke shell-based
isolation checks with a single, testable implementation.
