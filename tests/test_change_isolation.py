# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Tests for the change-isolation action logic."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import change_isolation as ci


# --------------------------------------------------------------------------
# Pure matching logic
# --------------------------------------------------------------------------


def test_parse_patterns_strips_and_drops_blanks():
    raw = "\n  /INFO.yaml \n\n.github/**\n   \n"
    assert ci.parse_patterns(raw) == ["/INFO.yaml", ".github/**"]


def classify(files, patterns):
    return ci.classify(files, ci.build_spec(patterns))


def test_no_in_scope_files_is_noop_pass():
    result = classify(["README.md", "src/app.py"], ["/INFO.yaml"])
    assert result.scanned is False
    assert result.isolated is True
    assert result.violating == []


def test_only_in_scope_file_passes():
    result = classify(["INFO.yaml"], ["/INFO.yaml"])
    assert result.scanned is True
    assert result.isolated is True
    assert result.matched == ["INFO.yaml"]
    assert result.violating == []


def test_in_scope_combined_with_out_of_scope_fails():
    result = classify(["INFO.yaml", "README.md"], ["/INFO.yaml"])
    assert result.scanned is True
    assert result.isolated is False
    assert result.matched == ["INFO.yaml"]
    assert result.violating == ["README.md"]


def test_anchored_pattern_only_matches_root():
    # A leading slash anchors to the repository root.
    result = classify(["src/INFO.yaml"], ["/INFO.yaml"])
    assert result.scanned is False
    assert result.isolated is True


def test_bare_pattern_matches_any_depth():
    result = classify(["src/INFO.yaml"], ["INFO.yaml"])
    assert result.scanned is True
    assert result.matched == ["src/INFO.yaml"]


def test_github_subtree_isolated():
    files = [".github/workflows/ci.yaml", ".github/dependabot.yml"]
    result = classify(files, [".github/**"])
    assert result.isolated is True
    assert result.violating == []


def test_github_subtree_with_outside_file_fails():
    files = [".github/workflows/ci.yaml", "README.md"]
    result = classify(files, [".github/**"])
    assert result.isolated is False
    assert result.violating == ["README.md"]


def test_info_and_github_are_mutually_exclusive():
    # Running the two independent checks the way the reusable workflow will.
    files = ["INFO.yaml", ".github/workflows/ci.yaml"]
    info = classify(files, ["/INFO.yaml"])
    github = classify(files, [".github/**"])
    assert info.isolated is False  # INFO.yaml check trips on the .github file
    assert github.isolated is False  # .github check trips on INFO.yaml


def test_negation_excludes_path():
    files = [".github/workflows/ci.yaml", ".github/CODEOWNERS"]
    result = classify(files, [".github/**", "!.github/CODEOWNERS"])
    assert result.isolated is False
    assert result.violating == [".github/CODEOWNERS"]


def test_empty_change_is_noop():
    result = classify([], ["/INFO.yaml"])
    assert result.scanned is False
    assert result.isolated is True


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("true", True, True),
        ("false", True, False),
        ("", True, True),
        ("", False, False),
        ("YES", False, True),
        ("0", True, False),
    ],
)
def test_as_bool(value, default, expected):
    assert ci._as_bool(value, default=default) is expected


# --------------------------------------------------------------------------
# Output / annotation rendering
# --------------------------------------------------------------------------


def test_write_outputs_multiline_violations(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    result = ci.Result(
        scanned=True, isolated=False, matched=["INFO.yaml"], violating=["a", "b"]
    )
    ci.write_outputs(result)
    content = out.read_text(encoding="utf-8")
    assert "isolated=false\n" in content
    assert "scanned=true\n" in content
    # The heredoc delimiter is randomised per run to prevent output
    # injection, so assert its structure rather than a fixed value.
    match = re.search(r"^violating-files<<(\S+)$", content, re.MULTILINE)
    assert match is not None
    delimiter = match.group(1)
    assert f"violating-files<<{delimiter}\na\nb\n{delimiter}\n" in content


def test_render_summary_lists_violations():
    result = ci.Result(
        scanned=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
    )
    summary = ci.render_summary(result, ["/INFO.yaml"])
    assert "Not isolated" in summary
    assert "`README.md`" in summary


def test_emit_returns_failure_and_annotation(capsys):
    result = ci.Result(
        scanned=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
    )
    code = ci.emit(result, ["/INFO.yaml"], fail_on_violation=True)
    captured = capsys.readouterr().out
    assert code == 1
    assert "::error file=README.md::" in captured


def test_emit_warns_without_failing_when_disabled(capsys):
    result = ci.Result(
        scanned=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
    )
    code = ci.emit(result, ["/INFO.yaml"], fail_on_violation=False)
    captured = capsys.readouterr().out
    assert code == 0
    assert "::warning file=README.md::" in captured


def test_escape_helpers_cover_command_metacharacters():
    assert ci._escape_data("100%\nx\r") == "100%25%0Ax%0D"
    assert ci._escape_property("a:b,c%") == "a%3Ab%2Cc%25"


def test_emit_escapes_annotation_special_chars(capsys):
    nasty = "a,b:c%d.txt"
    result = ci.Result(
        scanned=True, isolated=False, matched=["INFO.yaml"], violating=[nasty]
    )
    ci.emit(result, ["/INFO.yaml"], fail_on_violation=True)
    captured = capsys.readouterr().out
    # The file= property must escape '%', ':' and ',' so a crafted filename
    # cannot corrupt or inject workflow commands.
    assert "file=a%2Cb%3Ac%25d.txt::" in captured
    assert "file=a,b:c%d.txt" not in captured


# --------------------------------------------------------------------------
# Git integration
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with a base commit and a change commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "INFO.yaml").write_text("project: x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_get_changed_files_single_commit(repo, monkeypatch):
    (repo / "INFO.yaml").write_text("project: y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    monkeypatch.chdir(repo)
    assert ci.get_changed_files("HEAD~1") == ["INFO.yaml"]


def test_validate_base_ref_rejects_missing(repo, monkeypatch):
    # Only the root commit exists, so HEAD~1 cannot resolve.
    monkeypatch.chdir(repo)
    with pytest.raises(ci.IsolationError):
        ci.validate_base_ref("HEAD~1")


def test_validate_base_ref_reports_non_repo(tmp_path, monkeypatch):
    # A directory that is not a git work tree should point at actions/checkout
    # rather than blaming the base-ref or fetch-depth.
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    with pytest.raises(ci.IsolationError, match="not a Git repository"):
        ci.validate_base_ref("HEAD~1")


def test_main_passes_for_isolated_info_change(repo, monkeypatch):
    (repo / "INFO.yaml").write_text("project: y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml")
    monkeypatch.delenv("INPUT_BASE_REF", raising=False)
    monkeypatch.delenv("INPUT_FAIL_ON_VIOLATION", raising=False)
    assert ci.main() == 0


def test_main_fails_for_combined_change(repo, monkeypatch):
    (repo / "INFO.yaml").write_text("project: y\n", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml")
    assert ci.main() == 1


def test_main_requires_paths(repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("INPUT_PATHS", "   ")
    assert ci.main() == 1
