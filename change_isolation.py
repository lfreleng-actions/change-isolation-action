# /// script
# requires-python = ">=3.10"
# dependencies = ["pathspec>=0.12"]
# ///
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Verify that a change isolates modifications to a set of paths.

The script computes the set of files changed between a base reference and
``HEAD`` and checks them against a list of gitignore-style patterns (the
"in-scope" paths). A change is considered *isolated* when, if any changed
file matches the in-scope patterns, then *every* changed file matches them.
If no changed file matches the patterns the check is a no-op and passes.

Pattern matching uses gitignore semantics (via ``pathspec``):

* A leading ``/`` anchors the pattern to the repository root, so
  ``/INFO.yaml`` matches only a top-level ``INFO.yaml``.
* A bare name such as ``INFO.yaml`` matches at any depth.
* ``**`` matches across directories, so ``.github/**`` matches everything
  beneath the ``.github`` directory.
* ``!`` negation is supported for advanced exclusions.

Inputs are read from ``INPUT_*`` environment variables (the GitHub Actions
convention) and results are written to the console, ``GITHUB_OUTPUT``,
``GITHUB_STEP_SUMMARY`` and as workflow annotations.
"""

from __future__ import annotations

import os
import secrets
import subprocess  # nosec B404 - git invocation with fixed, non-shell argv
import sys
from dataclasses import dataclass, field
from typing import Any, List, Sequence

from pathspec import PathSpec

DEFAULT_BASE_REF = "HEAD~1"


class IsolationError(RuntimeError):
    """Raised for unrecoverable, user-actionable configuration problems."""


@dataclass
class Result:
    """Outcome of an isolation evaluation."""

    scanned: bool
    isolated: bool
    matched: List[str] = field(default_factory=list)
    violating: List[str] = field(default_factory=list)


def parse_patterns(raw: str) -> List[str]:
    """Split the multiline ``paths`` input into individual pattern lines.

    Blank lines are dropped and surrounding whitespace is trimmed from each
    line, which suits patterns authored in a YAML block scalar. Gitignore's
    significant trailing-whitespace handling is therefore not honoured;
    comment (``#``) and negation (``!``) handling is left to ``pathspec``.
    """
    return [line.strip() for line in raw.splitlines() if line.strip()]


def build_spec(patterns: Sequence[str]) -> "PathSpec[Any]":
    """Compile gitignore-style patterns into a matcher.

    Prefer the modern ``gitignore`` factory and fall back to the older
    ``gitwildmatch`` name so the action works across the supported pathspec
    range without emitting deprecation warnings on newer releases.
    """
    try:
        return PathSpec.from_lines("gitignore", patterns)
    except (KeyError, LookupError):
        return PathSpec.from_lines("gitwildmatch", patterns)


def classify(changed_files: Sequence[str], spec: "PathSpec[Any]") -> Result:
    """Partition changed files into in-scope and out-of-scope sets.

    The change is isolated when nothing in scope changed (no-op) or when
    every changed file is in scope.
    """
    matched: List[str] = []
    violating: List[str] = []
    for path in changed_files:
        (matched if spec.match_file(path) else violating).append(path)
    scanned = bool(matched)
    isolated = (not scanned) or (not violating)
    return Result(
        scanned=scanned,
        isolated=isolated,
        matched=matched,
        violating=violating if scanned else [],
    )


def _run_git(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a git command, capturing output, without using a shell."""
    return subprocess.run(  # nosec B603 - fixed argv, no shell, trusted binary
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def validate_base_ref(base_ref: str) -> None:
    """Ensure the base reference resolves to a commit in local history.

    Two failure modes get dedicated, actionable messages: running outside a
    Git work tree (commonly a missing ``actions/checkout`` step), and a base
    commit that is absent (commonly a shallow checkout).
    """
    inside = _run_git(["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise IsolationError(
            "the current directory is not a Git repository. Check out the "
            "repository first (for example with actions/checkout) before "
            "running this action."
        )
    completed = _run_git(["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"])
    if completed.returncode != 0:
        raise IsolationError(
            f"base-ref '{base_ref}' could not be resolved to a commit. "
            "If the repository was checked out shallowly, increase the "
            "checkout fetch-depth (e.g. fetch-depth: 2) or pass an explicit "
            "'base-ref' that exists in local history."
        )


def get_changed_files(base_ref: str) -> List[str]:
    """Return the files changed between ``base_ref`` and ``HEAD``.

    Uses a three-dot (merge-base) diff so that, when ``base-ref`` is a branch
    that advanced after the change branched off, only the files the change
    itself introduces are considered. ``--no-renames`` keeps isolation strict
    by surfacing a rename as both its old and new path.
    """
    validate_base_ref(base_ref)
    completed = _run_git(["diff", "--name-only", "--no-renames", f"{base_ref}...HEAD"])
    if completed.returncode != 0:
        raise IsolationError(
            f"git diff against base-ref '{base_ref}' failed: {completed.stderr.strip()}"
        )
    return [line for line in completed.stdout.splitlines() if line]


def _as_bool(value: str, *, default: bool) -> bool:
    """Parse a GitHub Actions style boolean string."""
    normalised = value.strip().lower()
    if not normalised:
        return default
    return normalised in {"true", "1", "yes", "on"}


def _append_to_env_file(env_var: str, content: str) -> None:
    """Append content to a file named by an environment variable, if set."""
    path = os.environ.get(env_var)
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(content)


def _escape_data(value: str) -> str:
    """Escape a GitHub Actions workflow-command message payload."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    """Escape a GitHub Actions workflow-command property value."""
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def write_outputs(result: Result) -> None:
    """Publish step outputs via ``GITHUB_OUTPUT`` (heredoc-safe)."""
    # Randomise the heredoc delimiter per run so a changed path can never
    # match it and inject additional outputs into ``GITHUB_OUTPUT``.
    delimiter = f"ghadelim_change_isolation_{secrets.token_hex(16)}"
    lines = [
        f"isolated={str(result.isolated).lower()}\n",
        f"scanned={str(result.scanned).lower()}\n",
        f"violating-files<<{delimiter}\n",
        ("\n".join(result.violating) + "\n") if result.violating else "",
        f"{delimiter}\n",
    ]
    _append_to_env_file("GITHUB_OUTPUT", "".join(lines))


def render_summary(result: Result, patterns: Sequence[str]) -> str:
    """Build a human-readable markdown summary block."""
    status = "✅ Isolated" if result.isolated else "❌ Not isolated"
    rows = [
        "## 🔒 Change Isolation",
        "",
        f"**Result:** {status}",
        "",
        "| Property | Value |",
        "| --- | --- |",
        f"| In-scope patterns | {len(patterns)} |",
        f"| In-scope files changed | {len(result.matched)} |",
        f"| Out-of-scope files changed | {len(result.violating)} |",
        f"| Scanned | {str(result.scanned).lower()} |",
        "",
    ]
    if result.violating:
        rows.append("### Out-of-scope files (break isolation)")
        rows.append("")
        rows.extend(f"- `{path}`" for path in result.violating)
        rows.append("")
    return "\n".join(rows) + "\n"


def emit(result: Result, patterns: Sequence[str], *, fail_on_violation: bool) -> int:
    """Write console output, summary, annotations and return an exit code."""
    print("🔒 Change Isolation check")
    print(f"  Patterns ({len(patterns)}):")
    for pattern in patterns:
        print(f"    - {pattern}")
    print(f"  In-scope files changed: {len(result.matched)}")
    for path in result.matched:
        print(f"    + {path}")

    _append_to_env_file("GITHUB_STEP_SUMMARY", render_summary(result, patterns))
    write_outputs(result)

    if result.isolated:
        if result.scanned:
            print("Result: isolated ✅ (all changed files are in scope)")
        else:
            print("Result: isolated ✅ (no in-scope files changed; no-op)")
        return 0

    print("Result: NOT isolated ❌")
    print("  Out-of-scope files that must not be combined with this change:")
    for path in result.violating:
        print(f"    - {path}")
        annotation = "error" if fail_on_violation else "warning"
        message = (
            f"File '{path}' is outside the isolated path set and must not "
            "be combined with these changes"
        )
        print(f"::{annotation} file={_escape_property(path)}::{_escape_data(message)}")

    if fail_on_violation:
        return 1
    print("fail-on-violation is false; reporting violation without failing ⚠️")
    return 0


def main() -> int:
    """Entry point: read inputs, evaluate isolation, emit results."""
    raw_paths = os.environ.get("INPUT_PATHS", "")
    base_ref = os.environ.get("INPUT_BASE_REF", "").strip() or DEFAULT_BASE_REF
    fail_on_violation = _as_bool(
        os.environ.get("INPUT_FAIL_ON_VIOLATION", ""), default=True
    )

    patterns = parse_patterns(raw_paths)
    if not patterns:
        print(
            "::error::The 'paths' input is required and must contain at least "
            "one gitignore-style pattern"
        )
        return 1

    try:
        changed_files = get_changed_files(base_ref)
    except IsolationError as exc:
        print(f"::error::{_escape_data(str(exc))}")
        return 1

    spec = build_spec(patterns)
    result = classify(changed_files, spec)
    return emit(result, patterns, fail_on_violation=fail_on_violation)


if __name__ == "__main__":
    sys.exit(main())
