"""Worktree 名称校验与自动命名。"""

import pytest

from codepilot.worktree.naming import WorktreeNameError, auto_name, validate_name


@pytest.mark.parametrize("name", [
    "a",
    "auth-fix",
    "a.b_c-1",
    "feature/auth-refactor",
    "a/b/c",
    "x" * 32,
    "a/" + "b" * 31,
])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", [
    "",
    " ",
    " a",
    "a ",
    ".",
    "..",
    "a/../b",
    "../x",
    "/abs",
    "C:/x",
    "a\\b",
    "A",
    "a/b/c/d",
    "a//b",
    "a/",
    "_meta",
    "con",
    "CON.txt",
    "nul.md",
    "com1",
    "lpt9.log",
    "a.",
    "a./b",
    "x" * 33,
    "a/" + "b" * 33,
    "a" * 65,
    "a b",
    "a@b",
    "-a",
    ".a",
])
def test_invalid_names(name):
    with pytest.raises(WorktreeNameError):
        validate_name(name)


def test_non_string_rejected():
    with pytest.raises(WorktreeNameError):
        validate_name(123)  # type: ignore[arg-type]


def test_auto_name_basic():
    assert auto_name("architect", 7) == "architect-7"
    assert auto_name("Code-Reviewer", 2) == "code-reviewer-2"


def test_auto_name_slugifies_non_ascii():
    assert auto_name("代码审查", 3) == "subagent-3"


def test_auto_name_respects_segment_limit():
    name = auto_name("a" * 100, 42)
    assert validate_name(name) == name
    assert len(name.split("/")[0]) <= 32
