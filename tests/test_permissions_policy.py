"""测试 PermissionManager — 五层/优先级/人机/会话/永久/拒绝。"""

import os

import pytest

from codepilot.permissions import PermissionManager, Verdict
from codepilot.permissions.mode import PermissionMode
from codepilot.permissions.policy import HumanChoice
from codepilot.permissions.rules import Rule


async def _check(pm, tool, **kw):
    return await pm.check(tool, kw)


class TestBlacklistHard:
    @pytest.mark.asyncio
    async def test_highrisk_denied_even_with_allow_rule(self, tmp_path):
        pm = PermissionManager(
            tmp_path, mode=PermissionMode.PERMISSIVE,
            rules=[Rule("run", "*", Verdict.ALLOW)],
        )
        d = await _check(pm, "run", command="rm -rf /")
        assert not d.allow
        assert "黑名单" in d.reason

    @pytest.mark.asyncio
    async def test_normal_command_not_blacklisted(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.PERMISSIVE)
        d = await _check(pm, "run", command="git status")
        assert d.allow


class TestSandbox:
    @pytest.mark.asyncio
    async def test_escape_denied(self, tmp_path):
        pm = PermissionManager(tmp_path)
        d = await _check(pm, "write", file_path="../evil.txt", content="x")
        assert not d.allow
        assert "越界" in d.reason

    @pytest.mark.asyncio
    async def test_inside_allowed(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.PERMISSIVE)
        assert (await _check(pm, "read", file_path="a.py")).allow


class TestPriority:
    @pytest.mark.asyncio
    async def test_deny_over_allow(self, tmp_path):
        pm = PermissionManager(tmp_path, rules=[
            Rule("run", "git *", Verdict.ALLOW),
            Rule("run", "git push *", Verdict.DENY),
        ])
        assert (await _check(pm, "run", command="git log")).allow
        assert not (await _check(pm, "run", command="git push origin main")).allow

    @pytest.mark.asyncio
    async def test_ask_over_allow(self, tmp_path):
        async def human(t, s):
            return HumanChoice.DENY
        pm = PermissionManager(tmp_path, rules=[
            Rule("run", "git *", Verdict.ALLOW),
            Rule("run", "git rm *", Verdict.ASK),
        ], human_callback=human)
        d = await _check(pm, "run", command="git rm x.py")
        assert not d.allow  # ask → human 拒绝


class TestModeFallback:
    @pytest.mark.asyncio
    async def test_strict_denies_unmatched(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.STRICT)
        assert not (await _check(pm, "run", command="echo hi")).allow

    @pytest.mark.asyncio
    async def test_default_asks_unmatched(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT,
                              human_callback=lambda t, s: None)
        # 无 human → ask 无法通过
        assert not (await _check(pm, "run", command="echo hi")).allow

    @pytest.mark.asyncio
    async def test_permissive_allows_unmatched(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.PERMISSIVE)
        assert (await _check(pm, "run", command="echo hi")).allow


class TestPermissiveDoesNotOverrideDeny:
    @pytest.mark.asyncio
    async def test_explicit_deny_wins_in_permissive(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.PERMISSIVE,
                               rules=[Rule("write", "secrets/*", Verdict.DENY)])
        d = await _check(pm, "write", file_path="secrets/key.txt", content="x")
        assert not d.allow


class TestHumanInLoop:
    @pytest.mark.asyncio
    async def test_allow_this_only_once(self, tmp_path):
        calls = []
        async def human(t, s):
            calls.append(s)
            return HumanChoice.ALLOW_THIS
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT, human_callback=human)
        assert (await _check(pm, "run", command="echo hi")).allow
        # ALLOW_THIS 不写会话缓存 → 第二次仍 ask
        assert (await _check(pm, "run", command="echo hi")).allow
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_allow_session_caches(self, tmp_path):
        calls = []
        async def human(t, s):
            calls.append(s)
            return HumanChoice.ALLOW_SESSION
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT, human_callback=human)
        assert (await _check(pm, "run", command="echo hi")).allow
        assert (await _check(pm, "run", command="echo hi")).allow
        assert len(calls) == 1  # 第二次命中会话缓存不再 ask

    @pytest.mark.asyncio
    async def test_allow_forever_persists_to_local(self, tmp_path):
        local = tmp_path / "local.yaml"
        async def human(t, s):
            return HumanChoice.ALLOW_FOREVER
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT,
                               human_callback=human, local_rules_path=local)
        assert (await _check(pm, "run", command="git log")).allow
        assert (await _check(pm, "run", command="git log")).allow
        assert local.exists()
        text = local.read_text(encoding="utf-8")
        assert "run(git log): allow" in text

    @pytest.mark.asyncio
    async def test_deny_returns(self, tmp_path):
        async def human(t, s):
            return HumanChoice.DENY
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT, human_callback=human)
        d = await _check(pm, "run", command="echo hi")
        assert not d.allow
        assert "拒绝" in d.reason

    @pytest.mark.asyncio
    async def test_no_human_callback_denies(self, tmp_path):
        pm = PermissionManager(tmp_path, mode=PermissionMode.DEFAULT)
        d = await _check(pm, "run", command="echo hi")
        assert not d.allow