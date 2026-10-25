# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Match repository paths against gitignore-style patterns.

A standard-library port of ``pathspec``'s ``gitignore`` pattern, kept
identical to it so the action needs no third-party package at runtime:

* A leading ``/`` anchors a pattern to the root; a pattern with no other
  ``/`` matches at any depth.
* ``**`` matches across directories at the start, middle or end.
* ``*``, ``?`` and ``[...]`` classes match within one path segment.
* A trailing ``/`` matches everything beneath a directory.
* ``!`` negates, and the last matching pattern wins.
* A backslash escapes the next character; blank lines and ``#`` comments
  are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence


class PatternError(ValueError):
    """Raised when a ``paths`` pattern cannot be compiled."""


# Wildcards that never match a slash, as regular expressions.
_WILDCARDS = {"*": "[^/]*", "?": "[^/]"}


def _translate_bracket(segment: str, start: int) -> tuple[str, int]:
    """Translate the bracket expression whose ``[`` precedes ``start``.

    Returns the regex and the index just past the expression. ``!`` or
    ``^`` first negates it, and a ``]`` first is a literal. Without a
    closing bracket the ``[`` is a literal and nothing else is consumed.
    """
    end = len(segment)
    j = start
    if j < end and segment[j] in "!^":
        j += 1
    if j < end and segment[j] == "]":
        j += 1
    while j < end and segment[j] != "]":
        j += 1
    if j >= end:
        return "\\[", start
    body = segment[start : j + 1]
    negate = body[0] in "!^"
    if negate:
        body = body[1:]
    return "[" + ("^" if negate else "") + body.replace("\\", "\\\\"), j + 1


def _translate_glob(segment: str) -> str:
    """Translate one path segment of a glob into a regular expression.

    ``*`` and ``?`` never match a slash, and ``[...]`` is a bracket
    expression. A backslash escapes the next character, and a trailing
    backslash is an error.
    """
    regex: list[str] = []
    i, end = 0, len(segment)
    while i < end:
        char = segment[i]
        i += 1
        if char == "\\":
            if i == end:
                raise ValueError("trailing backslash escapes nothing")
            regex.append(re.escape(segment[i]))
            i += 1
        elif char == "[":
            expr, i = _translate_bracket(segment, i)
            regex.append(expr)
        else:
            regex.append(_WILDCARDS.get(char) or re.escape(char))
    return "".join(regex)


def _normalise_segments(segments: list[str], *, is_dir: bool) -> list[str] | str:
    """Anchor and tidy pattern segments, or return a whole-pattern regex.

    A leading ``/`` anchors to the root; a single segment, with or without
    a trailing ``/``, matches at any depth; a trailing ``/`` matches
    everything beneath; repeated ``**`` segments collapse into one.
    """
    if not segments[0]:
        del segments[0]
    elif segments[0] != "**" and (
        len(segments) == 1 or (len(segments) == 2 and not segments[1])
    ):
        segments.insert(0, "**")
    if not segments:
        raise ValueError("pattern is empty once normalised")
    if not segments[-1]:
        segments[-1] = "**"
    for i in range(len(segments) - 1, 0, -1):
        if segments[i - 1] == "**" and segments[i] == "**":
            del segments[i]
    if segments == ["**"]:
        # '**/' matches everything outside the root; '**' matches everything.
        return "/" if is_dir else "."
    if segments == ["**", "*"]:
        return "."
    if segments == ["**", "*", "**"]:
        return "/"
    return segments


def _segments_to_regex(segments: Sequence[str]) -> str:
    """Join normalised pattern segments into one regular expression."""
    parts: list[str] = []
    need_slash = False
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        if segment == "**":
            if i == 0:
                parts.append("^(?:.+/)?")
            elif i < last:
                parts.append("(?:/.+)?")
                need_slash = True
            else:
                parts.append("/")
            continue
        if i == 0:
            parts.append("^")
        if need_slash:
            parts.append("/")
        parts.append("[^/]+" if segment == "*" else _translate_glob(segment))
        if i == last:
            # A final '*' matches one entry; any other final segment also
            # matches everything beneath it.
            parts.append("/?$" if segment == "*" else "(?:/|$)")
        need_slash = True
    return "".join(parts)


def compile_pattern(pattern: str) -> tuple[re.Pattern[str], bool] | None:
    """Compile one gitignore-style pattern into a regex and an include flag.

    Returns ``None`` for a blank line or a ``#`` comment. The include flag
    is ``False`` for a ``!`` negation. Raises :class:`PatternError` for a
    pattern that cannot match anything well-defined.
    """
    text = pattern if pattern.endswith("\\ ") else pattern.rstrip()
    if not text or text.startswith("#"):
        return None
    include = not text.startswith("!")
    if not include:
        text = text[1:]
    segments = text.split("/")
    # A lone '/' is treated as '**' rather than as the directory form '**/'.
    is_dir = not segments[-1] and text != "/"
    try:
        normalised = _normalise_segments(segments, is_dir=is_dir)
        regex = (
            normalised
            if isinstance(normalised, str)
            else _segments_to_regex(normalised)
        )
        return re.compile(regex), include
    except (ValueError, re.error) as exc:
        raise PatternError(f"invalid pattern '{pattern}': {exc}") from exc


def _normalise_path(path: str) -> str:
    """Drop one leading ``/`` or ``./`` so the path is root-relative."""
    if path.startswith("/"):
        return path[1:]
    if path.startswith("./"):
        return path[2:]
    return path


class GitIgnoreMatcher:
    """Match repository paths against an ordered list of gitignore patterns.

    The last pattern that matches a path decides it: a plain pattern
    includes the path and a ``!`` pattern excludes it again. A path that
    no pattern matches is excluded.
    """

    def __init__(self, rules: Sequence[tuple[re.Pattern[str], bool]]) -> None:
        """Store compiled ``(regex, include)`` rules, in pattern order."""
        self._rules = list(reversed(rules))

    @classmethod
    def from_lines(cls, patterns: Iterable[str]) -> GitIgnoreMatcher:
        """Compile pattern lines, skipping blanks and comments."""
        rules = [rule for line in patterns if (rule := compile_pattern(line))]
        return cls(rules)

    def match_file(self, path: str) -> bool:
        """Return whether ``path`` is in the set the patterns describe."""
        normalised = _normalise_path(path)
        for regex, include in self._rules:
            if regex.search(normalised):
                return include
        return False
