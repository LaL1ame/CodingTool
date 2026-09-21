"""测试规则模型 — 解析/verdict/glob/精确/merge。"""

import pytest

from codepilot.permissions.rules import (
    Rule, Verdict, match_text, merge, parse_rule,
)


class TestParseRule:
    def test_basic(self):
        r = parse_rule("run(git *): allow")
        assert r == Rule("run", "git *", Verdict.ALLOW)

    def test_default_verdict_allow(self):
        r = parse_rule("run(git *)")
        assert r.verdict == Verdict.ALLOW

    def test_verdict_all_three(self):
        assert parse_rule("write(**)").verdict == Verdict.ALLOW
        assert parse_rule("write(**): ask").verdict == Verdict.ASK
        assert parse_rule("write(**): deny").verdict == Verdict.DENY

    def test_price_strips_spaces(self):
        r = parse_rule("  run( git status ) : allow ")
        assert r.tool == "run"
        assert r.pattern == "git status"

    def test_invalid_missing_parens(self):
        with pytest.raises(ValueError):
            parse_rule("run git *")


class TestMatchText:
    def test_exact_match(self):
        assert match_text("git status", "git status")

    def test_exact_miss(self):
        assert not match_text("git status", "git log")

    def test_glob_wildcard(self):
        assert match_text("git *", "git log --oneline")
        assert match_text("git *", "git status")

    def test_glob_miss(self):
        assert not match_text("git *", "rm -rf")

    def test_none_subject(self):
        assert not match_text("git *", None)

    def test_glob_char_class(self):
        assert match_text("cat ?.txt", "cat a.txt")


class TestMerge:
    def test_dedupe(self):
        a = [Rule("run", "git *", Verdict.ALLOW)]
        b = [Rule("run", "git *", Verdict.ALLOW), Rule("run", "rm *", Verdict.DENY)]
        merged = merge([a, b])
        assert len(merged) == 2
        assert Rule("run", "rm *", Verdict.DENY) in merged