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
    assert result.triggered is False
    assert result.isolated is True
    assert result.violating == []


def test_only_in_scope_file_passes():
    result = classify(["INFO.yaml"], ["/INFO.yaml"])
    assert result.triggered is True
    assert result.isolated is True
    assert result.matched == ["INFO.yaml"]
    assert result.violating == []


def test_in_scope_combined_with_out_of_scope_fails():
    result = classify(["INFO.yaml", "README.md"], ["/INFO.yaml"])
    assert result.triggered is True
    assert result.isolated is False
    assert result.matched == ["INFO.yaml"]
    assert result.violating == ["README.md"]


def test_anchored_pattern_only_matches_root():
    # A leading slash anchors to the repository root.
    result = classify(["src/INFO.yaml"], ["/INFO.yaml"])
    assert result.triggered is False
    assert result.isolated is True


def test_bare_pattern_matches_any_depth():
    result = classify(["src/INFO.yaml"], ["INFO.yaml"])
    assert result.triggered is True
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
    assert result.triggered is False
    assert result.isolated is True


def test_classify_records_evaluated_files_on_noop():
    # The evaluated list is reported whatever the outcome, no-op included.
    result = classify(["README.md", "src/app.py"], ["/INFO.yaml"])
    assert result.triggered is False
    assert result.changed == ["README.md", "src/app.py"]


# --------------------------------------------------------------------------
# changed-files input parsing
# --------------------------------------------------------------------------


def test_parse_changed_files_strips_whitespace_and_blank_lines():
    raw = "\n  INFO.yaml \r\n\n\t.github/workflows/ci.yaml\t\n   \n"
    assert ci.parse_changed_files(raw) == ["INFO.yaml", ".github/workflows/ci.yaml"]


def test_parse_changed_files_takes_entries_as_is():
    # No normalisation: entries match what git diff --name-only emits.
    raw = "./INFO.yaml\nsrc//app.py\nsrc/app.py\nsrc/app.py\nname with space.txt"
    assert ci.parse_changed_files(raw) == [
        "./INFO.yaml",
        "src//app.py",
        "src/app.py",
        "src/app.py",
        "name with space.txt",
    ]


def test_parse_changed_files_empty_input_yields_empty_list():
    assert ci.parse_changed_files("") == []
    assert ci.parse_changed_files(" \n\t\n") == []


@pytest.mark.parametrize(
    "bad", ["a\tb.txt", "a\x00b", "a\x1b[31mb", "a\x7fb", "a\x85b"]
)
def test_parse_changed_files_refuses_control_characters(bad):
    with pytest.raises(ci.IsolationError, match="line 2"):
        ci.parse_changed_files(f"INFO.yaml\n{bad}\n")


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
        triggered=True, isolated=False, matched=["INFO.yaml"], violating=["a", "b"]
    )
    ci.write_outputs(result)
    content = out.read_text(encoding="utf-8")
    assert "isolated=false\n" in content
    assert "triggered=true\n" in content
    # 'scanned' is a deprecated alias for 'triggered' kept for existing
    # consumers of the action's outputs.
    assert "scanned=true\n" in content
    # The heredoc delimiter is randomised per run to prevent output
    # injection, so assert its structure rather than a fixed value.
    match = re.search(r"^violating-files<<(\S+)$", content, re.MULTILINE)
    assert match is not None
    delimiter = match.group(1)
    assert f"violating-files<<{delimiter}\na\nb\n{delimiter}\n" in content


def test_write_outputs_includes_changed_files(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    result = ci.Result(
        triggered=False,
        isolated=True,
        changed=["README.md", "isolated=true"],
    )
    ci.write_outputs(result)
    content = out.read_text(encoding="utf-8")
    match = re.search(r"^changed-files<<(\S+)$", content, re.MULTILINE)
    assert match is not None
    delimiter = match.group(1)
    # A path that looks like an output assignment stays inside the heredoc.
    assert f"changed-files<<{delimiter}\nREADME.md\nisolated=true\n{delimiter}\n" in (
        content
    )


def test_write_outputs_empty_changed_files(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci.write_outputs(ci.Result(triggered=False, isolated=True))
    content = out.read_text(encoding="utf-8")
    match = re.search(r"^changed-files<<(\S+)$", content, re.MULTILINE)
    assert match is not None
    assert f"changed-files<<{match.group(1)}\n{match.group(1)}\n" in content


def test_render_summary_lists_violations():
    result = ci.Result(
        triggered=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
    )
    summary = ci.render_summary(result, ["/INFO.yaml"])
    assert "Not isolated" in summary
    assert "`README.md`" in summary


def test_render_summary_shows_patterns():
    # The summary table must show the patterns themselves so that runs
    # invoking the action multiple times remain distinguishable.
    result = ci.Result(triggered=False, isolated=True)
    summary = ci.render_summary(result, ["/INFO.yaml", ".github/**"])
    assert "| In-scope patterns | `/INFO.yaml`<br>`.github/**` |" in summary


def test_render_summary_labels_noop_pass():
    result = ci.Result(triggered=False, isolated=True)
    summary = ci.render_summary(result, ["/INFO.yaml"])
    assert "✅ Isolated (no in-scope files changed; no-op)" in summary
    assert "| Triggered | false |" in summary


def test_render_summary_states_changed_files_source():
    result = ci.Result(triggered=False, isolated=True)
    summary = ci.render_summary(
        result, ["/INFO.yaml"], source="`changed-files` input (`base-ref` unused)"
    )
    assert (
        "| Changed files source | `changed-files` input (`base-ref` unused) |"
        in summary
    )


def test_render_summary_escapes_pipes_in_patterns():
    # A pipe in a pattern must not break the markdown table structure.
    result = ci.Result(triggered=False, isolated=True)
    summary = ci.render_summary(result, ["weird|name"])
    assert "`weird\\|name`" in summary


def test_code_span_handles_embedded_backticks():
    # A backtick run in the value must not terminate the code span; the
    # delimiter grows beyond the longest run and edge backticks get padded.
    assert ci._code_span("plain") == "`plain`"
    assert ci._code_span("a`b") == "``a`b``"
    assert ci._code_span("a``b") == "```a``b```"
    assert ci._code_span("`edge`") == "`` `edge` ``"


def test_render_summary_survives_backticks_in_patterns_and_files():
    result = ci.Result(
        triggered=True,
        isolated=False,
        matched=["INFO.yaml"],
        violating=["weird`file.txt"],
    )
    summary = ci.render_summary(result, ["pat`tern"])
    assert "``pat`tern``" in summary
    assert "- ``weird`file.txt``" in summary


def test_emit_returns_failure_and_annotation(capsys):
    result = ci.Result(
        triggered=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
    )
    code = ci.emit(result, ["/INFO.yaml"], fail_on_violation=True)
    captured = capsys.readouterr().out
    assert code == 1
    assert "::error file=README.md::" in captured


def test_emit_warns_without_failing_when_disabled(capsys):
    result = ci.Result(
        triggered=True, isolated=False, matched=["INFO.yaml"], violating=["README.md"]
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
        triggered=True, isolated=False, matched=["INFO.yaml"], violating=[nasty]
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


# --------------------------------------------------------------------------
# changed-files input: precedence over git
# --------------------------------------------------------------------------


def _forbid_git(monkeypatch):
    def fail(args):
        raise AssertionError(f"git must not run with changed-files set: {args}")

    monkeypatch.setattr(ci, "_run_git", fail)


def _output_value(path: Path, name: str) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}<<(\S+)\n(.*?)^\1$", content, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"output {name!r} missing"
    return match.group(2)


def test_main_uses_changed_files_without_git(tmp_path, monkeypatch):
    # No work tree and an unresolvable base-ref: the input alone decides.
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    _forbid_git(monkeypatch)
    out = tmp_path / "out"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml")
    monkeypatch.setenv("INPUT_BASE_REF", "does-not-exist")
    monkeypatch.setenv("INPUT_CHANGED_FILES", "INFO.yaml\nsrc/app.py\n")
    monkeypatch.delenv("INPUT_FAIL_ON_VIOLATION", raising=False)
    assert ci.main() == 1
    assert _output_value(out, "changed-files") == "INFO.yaml\nsrc/app.py\n"
    assert _output_value(out, "violating-files") == "src/app.py\n"
    assert "`base-ref` unused" in summary.read_text(encoding="utf-8")


def test_main_changed_files_take_precedence_over_base_ref(repo, monkeypatch, tmp_path):
    # git would report a combined change; the supplied list is isolated.
    (repo / "INFO.yaml").write_text("project: y\n", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    _forbid_git(monkeypatch)
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml")
    monkeypatch.setenv("INPUT_BASE_REF", "HEAD~1")
    monkeypatch.setenv("INPUT_CHANGED_FILES", "INFO.yaml")
    assert ci.main() == 0


def test_main_refuses_control_characters_in_changed_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _forbid_git(monkeypatch)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("INPUT_PATHS", "/INFO.yaml")
    monkeypatch.setenv("INPUT_CHANGED_FILES", "INFO.yaml\nevil\x1b]8;;x\n")
    assert ci.main() == 1
    assert not out.exists()


def test_main_blank_changed_files_falls_back_to_git(repo, monkeypatch, tmp_path):
    (repo / "INFO.yaml").write_text("project: y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    monkeypatch.chdir(repo)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("INPUT_PATHS", "/README.md")
    monkeypatch.setenv("INPUT_CHANGED_FILES", " \n\n")
    monkeypatch.delenv("INPUT_BASE_REF", raising=False)
    assert ci.main() == 0
    # Emitted from git on the no-op path too.
    assert _output_value(out, "changed-files") == "INFO.yaml\n"
