# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Tests for the standard-library gitignore matcher.

The differential tests hold the matcher to ``pathspec``'s ``gitignore``
pattern, the matcher the action used before it dropped its runtime
dependency. ``pathspec`` is a test-only dependency for that purpose.
"""

from __future__ import annotations

import itertools
import os
import random
import subprocess
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

import pytest
from pathspec import PathSpec

import change_isolation as ci
import gitignore_matcher

SCRIPT = Path(ci.__file__).resolve()

# Paths as git lists them, plus a few the changed-files input may carry.
PATHS = [
    "INFO.yaml",
    "src/INFO.yaml",
    "a/b/INFO.yaml",
    "INFO.yaml.bak",
    "README.md",
    "docs/README.md",
    "docs/guide/index.md",
    "docs",
    "doc",
    "src/app.py",
    "src/pkg/mod.py",
    "src/pkg/sub/deep.py",
    "src/app.pyc",
    "test.py",
    "tests/test_x.py",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/workflows/ci.yaml",
    ".github/workflows/sub/x.yaml",
    "x/.github/workflows/ci.yaml",
    ".gitignore",
    ".env",
    "config/.env",
    ".hidden/file",
    "a",
    "a/b",
    "a/b/c",
    "a/x/b",
    "a/x/y/b",
    "a/b/c/d",
    "b/a",
    "ab",
    "abc",
    "a.txt",
    "b.txt",
    "c.txt",
    "-.txt",
    "#notes",
    "!bang",
    "[x]",
    "x",
    "foo bar",
    "build/out",
    "lib/build/out",
    "./INFO.yaml",
    "/INFO.yaml",
    "/src/app.py",
    "a//b",
]

PATTERNS = [
    "/INFO.yaml",
    "INFO.yaml",
    ".github/**",
    ".github/",
    ".github",
    "/.github/",
    "**/INFO.yaml",
    "**/workflows/*.yaml",
    "a/**/b",
    "a/**",
    "**/b",
    "**",
    "**/",
    "*",
    "*/",
    "/*",
    "*.py",
    "src/*.py",
    "src/**/*.py",
    "src/*",
    "src/",
    "/src",
    "docs/**/index.md",
    "?.txt",
    "[ab].txt",
    "[!ab].txt",
    "[^ab].txt",
    "[a-c].txt",
    "[]-].txt",
    "[-].txt",
    "[",
    "a[",
    "[x]",
    "\\[x]",
    "\\#notes",
    "\\!bang",
    "#notes",
    "\\*",
    "foo\\ bar",
    "foo bar",
    ".*",
    "/.*",
    ".env",
    "build/",
    "/build/",
    "a/b/",
    "/",
    "//",
    "a//b",
    "***",
    "a/**/**/b",
    "**/**",
    "",
    "   ",
    "# comment",
]

REJECTED = ["!", "\\", "a\\", "!a\\"]


def _pathspec(patterns: Sequence[str], paths: Sequence[str]) -> list[bool] | None:
    """Return pathspec's verdicts, or ``None`` when it rejects the patterns."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            spec = PathSpec.from_lines("gitignore", patterns)
        except Exception:  # noqa: BLE001 - any rejection counts
            return None
        return [spec.match_file(path) for path in paths]


def _ours(patterns: Sequence[str], paths: Sequence[str]) -> list[bool] | None:
    """Return the matcher's verdicts, or ``None`` when it rejects them."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            matcher = ci.GitIgnoreMatcher.from_lines(patterns)
        except ci.PatternError:
            return None
        return [matcher.match_file(path) for path in paths]


def _assert_same(patterns: Sequence[str], paths: Sequence[str]) -> None:
    expected = _pathspec(patterns, paths)
    actual = _ours(patterns, paths)
    if expected is None or actual is None:
        assert actual == expected, f"rejection differs for {patterns!r}"
        return
    diff = [
        (path, want, got)
        for path, want, got in zip(paths, expected, actual)
        if want != got
    ]
    assert not diff, f"{patterns!r}: (path, pathspec, ours) {diff}"


# --------------------------------------------------------------------------
# Differential: identical results to pathspec
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", PATTERNS + REJECTED)
def test_single_pattern_matches_pathspec(pattern):
    _assert_same([pattern], PATHS)
    _assert_same(["!" + pattern], PATHS)


def test_pattern_pairs_match_pathspec():
    # Negation order: the last matching pattern wins.
    for first, second in itertools.product(PATTERNS, repeat=2):
        _assert_same([first, "!" + second], PATHS)
        _assert_same(["!" + second, first], PATHS)
        _assert_same([first, "!" + second, second], PATHS)


def test_generated_patterns_match_pathspec():
    rng = random.Random(88)
    pattern_alphabet = ["a", "b", ".", "*", "**", "?", "/", "[", "]", "!", "^"]
    pattern_alphabet += ["-", "\\", "#", " "]
    path_alphabet = ["a", "b", ".", "/", "-", "!", "#", "[", "]", " "]
    paths = sorted(
        {
            "".join(rng.choice(path_alphabet) for _ in range(rng.randint(1, 8)))
            for _ in range(300)
        }
    )
    paths += PATHS
    for _ in range(1500):
        count = rng.randint(1, 3)
        patterns = [
            "".join(rng.choice(pattern_alphabet) for _ in range(rng.randint(1, 7)))
            for _ in range(count)
        ]
        _assert_same(patterns, paths)


# --------------------------------------------------------------------------
# Documented semantics
# --------------------------------------------------------------------------


def _matches(patterns: Sequence[str], path: str) -> bool:
    return ci.GitIgnoreMatcher.from_lines(patterns).match_file(path)


@pytest.mark.parametrize(
    ("patterns", "path", "expected"),
    [
        (["/INFO.yaml"], "INFO.yaml", True),
        (["/INFO.yaml"], "src/INFO.yaml", False),
        (["INFO.yaml"], "a/b/INFO.yaml", True),
        (["INFO.yaml"], "INFO.yaml.bak", False),
        ([".github/**"], ".github/workflows/ci.yaml", True),
        ([".github/**"], "x/.github/workflows/ci.yaml", False),
        (["**/ci.yaml"], "x/.github/workflows/ci.yaml", True),
        (["a/**/b"], "a/b", True),
        (["a/**/b"], "a/x/y/b", True),
        (["*.py"], "src/pkg/mod.py", True),
        (["src/*.py"], "src/pkg/mod.py", False),
        (["?.txt"], "a.txt", True),
        (["?.txt"], "ab.txt", False),
        (["[ab].txt"], "b.txt", True),
        (["[!ab].txt"], "b.txt", False),
        (["[!ab].txt"], "c.txt", True),
        (["build/"], "lib/build/out", True),
        (["build/"], "build", False),
        (["*"], ".env", True),
        ([".github/**", "!.github/CODEOWNERS"], ".github/CODEOWNERS", False),
        ([".github/**", "!.github/CODEOWNERS"], ".github/ci.yaml", True),
        (["!.github/CODEOWNERS", ".github/**"], ".github/CODEOWNERS", True),
        (["*.md", "!docs/*.md", "docs/README.md"], "docs/README.md", True),
        (["\\#notes"], "#notes", True),
        (["\\!bang"], "!bang", True),
        (["\\*"], "x", False),
        (["#notes"], "#notes", False),
        (["", "# comment"], "anything", False),
        (["/INFO.yaml"], "./INFO.yaml", True),
        (["/INFO.yaml"], "/INFO.yaml", True),
    ],
)
def test_documented_semantics(patterns, path, expected):
    assert _matches(patterns, path) is expected


@pytest.mark.parametrize("pattern", REJECTED)
def test_invalid_pattern_is_rejected(pattern):
    with pytest.raises(ci.PatternError, match="invalid pattern"):
        ci.GitIgnoreMatcher.from_lines([pattern])


def test_main_reports_invalid_pattern(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml\nsrc\\")
    monkeypatch.setenv("INPUT_CHANGED_FILES", "INFO.yaml")
    assert ci.main() == 1
    assert "::error::invalid pattern 'src\\'" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


# --------------------------------------------------------------------------
# Native path separators
# --------------------------------------------------------------------------

# pathspec normalises the platform's own separators (os.sep and os.altsep,
# where they differ from '/') to '/' before matching. These tests stand in
# for Windows by setting the matcher's separators, and compare the result
# with pathspec given the same separators.
WINDOWS_SEPARATORS = ("\\",)

SEPARATOR_CASES = [
    ([".github/**"], ".github\\workflows\\ci.yaml"),
    ([".github/**"], "README.md"),
    (["/INFO.yaml"], "\\INFO.yaml"),
    (["/INFO.yaml"], ".\\INFO.yaml"),
    (["docs/"], "src\\docs\\index.md"),
    (["*.py", "!tests/**"], "tests\\test_x.py"),
    (["a/**/b"], "a\\x\\y\\b"),
]


@pytest.mark.parametrize(("patterns", "path"), SEPARATOR_CASES)
def test_native_separators_match_pathspec(monkeypatch, patterns, path):
    monkeypatch.setattr(gitignore_matcher, "NATIVE_SEPARATORS", WINDOWS_SEPARATORS)
    expected = PathSpec.from_lines("gitignore", patterns).match_file(
        path, separators=WINDOWS_SEPARATORS
    )
    assert ci.GitIgnoreMatcher.from_lines(patterns).match_file(path) is expected


def test_native_separators_cannot_hide_a_mixed_change(monkeypatch):
    # The reported case: a Windows-style in-scope path beside an
    # out-of-scope one must not pass as an untriggered, isolated change.
    monkeypatch.setattr(gitignore_matcher, "NATIVE_SEPARATORS", WINDOWS_SEPARATORS)
    spec = ci.build_spec([".github/**"])
    result = ci.classify([".github\\workflows\\ci.yaml", "README.md"], spec)
    assert result.triggered is True
    assert result.isolated is False
    assert result.violating == ["README.md"]


@pytest.mark.skipif(os.sep != "/" or bool(os.altsep), reason="POSIX only")
def test_backslash_stays_literal_on_posix():
    # Where '/' is the only separator, pathspec leaves a backslash alone,
    # so a path containing one names a single, oddly named file.
    patterns = [".github/**"]
    path = ".github\\workflows\\ci.yaml"
    expected = PathSpec.from_lines("gitignore", patterns).match_file(
        path, separators=()
    )
    assert expected is False
    assert ci.GitIgnoreMatcher.from_lines(patterns).match_file(path) is expected


# --------------------------------------------------------------------------
# Standard library only
# --------------------------------------------------------------------------


def test_script_runs_without_site_packages(tmp_path):
    # Mirrors the action: isolated mode, and -S keeps pathspec out of reach.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "INPUT_PATHS": ".github/**\n!.github/CODEOWNERS",
        "INPUT_CHANGED_FILES": ".github/workflows/ci.yaml\n.github/CODEOWNERS",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr
    assert "::error file=.github/CODEOWNERS::" in completed.stdout
    assert "violating-files<<" in (tmp_path / "out").read_text(encoding="utf-8")
