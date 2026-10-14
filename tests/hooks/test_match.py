"""state16 T1：共享匹配器四语法（精确 / !反向 / /regex/ / glob）。

权限规则与 Hook 条件共用 match_text；本文件同时承担权限既有行为的回归。
"""

from codepilot.permissions.rules import (
    is_regex_pattern,
    match_text,
    regex_body,
)


class TestExactAndGlob:
    """既有行为回归：无新语法前缀时与原语义一字不差。"""

    def test_exact_match(self):
        assert match_text("git status", "git status") is True

    def test_exact_no_match(self):
        assert match_text("git status", "git diff") is False

    def test_glob_match(self):
        assert match_text("git *", "git push origin main") is True

    def test_glob_no_match(self):
        assert match_text("git *", "npm install") is False

    def test_glob_question_and_class(self):
        assert match_text("file?.txt", "file1.txt") is True
        assert match_text("file[0-9].txt", "file7.txt") is True
        assert match_text("file[0-9].txt", "filex.txt") is False

    def test_subject_none(self):
        assert match_text("git *", None) is False
        assert match_text("!git *", None) is False

    def test_absolute_path_glob_not_treated_as_regex(self):
        # /etc/* 不以 / 结尾 → 仍是 glob，不是正则
        assert match_text("/etc/*", "/etc/hosts") is True
        assert match_text("/etc/*", "/home/x") is False


class TestNegation:
    def test_negate_exact(self):
        assert match_text("!git status", "git status") is False
        assert match_text("!git status", "git diff") is True

    def test_negate_glob(self):
        assert match_text("!git *", "npm install") is True
        assert match_text("!git *", "git push") is False

    def test_negate_regex(self):
        assert match_text("!/^rm -rf/", "ls -la") is True
        assert match_text("!/^rm -rf/", "rm -rf /tmp") is False


class TestRegex:
    def test_regex_search(self):
        assert match_text("/^rm -rf/", "rm -rf /tmp/x") is True
        assert match_text("/^rm -rf/", "ls rm -rf") is False  # ^ 锚定开头
        assert match_text("/rm -rf/", "ls rm -rf x") is True  # 无锚 → search 非 fullmatch
        assert match_text("/^rm -rf/", "git status") is False

    def test_regex_middle_anchor(self):
        assert match_text("/\\d{4}-\\d{2}/", "date 2026-10 ok") is True
        assert match_text("/\\d{4}-\\d{2}/", "no digits") is False

    def test_invalid_regex_no_match(self):
        # 运行时防御：非法正则 no-match（加载期由 hooks loader 校验拦截）
        assert match_text("/[unclosed/", "anything") is False

    def test_is_regex_pattern(self):
        assert is_regex_pattern("/abc/") is True
        assert is_regex_pattern("//") is True
        assert is_regex_pattern("/") is False
        assert is_regex_pattern("/etc/*") is False
        assert is_regex_pattern("abc") is False

    def test_regex_body(self):
        assert regex_body("/^rm -rf/") == "^rm -rf"
